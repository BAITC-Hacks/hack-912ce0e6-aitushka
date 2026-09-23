"""HTTP contracts exercised against reconciled Parquet fixtures."""

from concurrent.futures import ThreadPoolExecutor
import csv
from io import StringIO
import json
from pathlib import Path
import re

from fastapi.testclient import TestClient
import pytest

from money_graph import api


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api_client(data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("MONEY_GRAPH_DATA", str(data_dir))
    monkeypatch.setenv("MONEY_GRAPH_CONFIG", str(ROOT / "config.json"))
    monkeypatch.setenv("MONEY_GRAPH_OUT", str(tmp_path / "output-not-written-by-get"))
    with TestClient(api.create_app()) as client:
        yield client


def graph_ids(document):
    marker = re.search(r"const graphData\s*=\s*\{nodes:new vis\.DataSet\(", document)
    assert marker, "Graph response must contain the actual browser dataset"
    nodes, _ = json.JSONDecoder().raw_decode(document[marker.end():])
    assert all(isinstance(node["id"], str) for node in nodes)
    return {node["id"] for node in nodes}


def test_health_checks_dataset_and_overview_accounts_for_all_rows(api_client, valid_frames):
    edges, nodes, tx = valid_frames
    health = api_client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert len(health.json()["fingerprint"]) == 64
    response = api_client.get("/api/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["n_nodes"] == len(nodes)
    assert body["summary"]["n_edges"] == len(edges)
    assert body["summary"]["n_transactions"] == len(tx)
    assert body["summary"]["n_seed"] == 1
    assert body["summary"]["boundary_count"] == 1
    assert body["summary"]["sum_kzt"] == tx.sum_kzt.sum()
    assert body["summary"]["date_min"] == "2026-07-01"
    assert body["summary"]["date_max"] == "2026-07-06"
    assert sum(row["count"] for row in body["roles"]) == len(nodes)
    assert all(row["label"] and row["color"].startswith("#") for row in body["roles"])
    assert sum(row["n_nodes"] for row in body["clusters"]) == len(nodes)
    assert body["metadata"]["input_fingerprint"] == health.json()["fingerprint"]


def test_clients_pagination_preserves_numeric_gid_order_and_full_slice_counts(api_client):
    body = api_client.get("/api/clients?limit=1000").json()
    items = body["items"]
    assert body["total"] == 26
    assert len(items) == 26
    assert items == sorted(items, key=lambda row: (-row["priority_score"], int(row["gid"])))
    assert all(isinstance(row["gid"], str) for row in items)
    assert all(row["role"] and row["evidence"] and "contribution_brokerage" in row for row in items)
    partial = api_client.get("/api/clients?offset=3&limit=4").json()
    assert partial["items"] == items[3:7]
    assert partial["total"] == body["total"]
    assert partial["role_counts"] == body["role_counts"]
    assert sum(partial["role_counts"].values()) == 26
    assert api_client.get("/api/clients?offset=1000").json()["items"] == []


def test_filters_combine_and_use_entire_filtered_slice_for_role_counts(api_client, valid_frames):
    _, nodes, _ = valid_frames
    gid = str(nodes.gid.iloc[4])
    client = api_client.get(f"/api/clients/{gid}").json()["client"]
    query = {"roles": "peripheral", "clusters": str(client["cluster_id"]),
             "seed": "nonseed", "boundary": "boundary", "limit": 1}
    response = api_client.get("/api/clients", params=query)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [row["gid"] for row in body["items"]] == [gid]
    assert body["role_counts"] == {"peripheral": 1}
    assert api_client.get("/api/clients?seed=seed").json()["total"] == 1
    assert api_client.get("/api/clients?boundary=internal").json()["total"] == 25


def test_large_odd_isolate_is_found_exactly_outside_current_filters(api_client, valid_frames):
    _, nodes, _ = valid_frames
    gid = str(nodes.gid.iloc[-2])
    assert int(gid) > 2**53 and int(float(gid)) != int(gid)
    filtered = api_client.get("/api/clients?roles=terminal").json()
    assert gid not in {row["gid"] for row in filtered["items"]}
    response = api_client.get(f"/api/clients/{gid}")
    assert response.status_code == 200
    body = response.json()
    assert body["client"]["gid"] == gid
    assert body["client"]["pass_through"] is None
    assert body["incoming"] == body["outgoing"] == body["transactions"] == []
    assert all(row["in_kzt"] == row["out_kzt"] == 0 for row in body["daily"])
    assert "NaN" not in response.text and "Infinity" not in response.text
    graph = api_client.get("/api/graph", params={"gid": gid, "roles": "terminal"})
    assert graph.status_code == 200
    assert "text/html" in graph.headers["content-type"]
    assert graph_ids(graph.text) == {gid}


def test_client_detail_returns_exact_edge_ids_and_reconciled_daily_totals(api_client, valid_frames):
    _, nodes, _ = valid_frames
    gid = str(nodes.gid.iloc[1])
    body = api_client.get(f"/api/clients/{gid}").json()
    assert body["client"]["gid"] == gid
    assert len(body["incoming"]) == 1
    assert len(body["outgoing"]) == 2
    for row in body["incoming"] + body["outgoing"] + body["transactions"]:
        assert isinstance(row["src"], str) and isinstance(row["dst"], str)
    assert sum(row["in_kzt"] for row in body["daily"]) == body["client"]["in_kzt"]
    assert sum(row["out_kzt"] for row in body["daily"]) == body["client"]["out_kzt"]
    assert len(body["daily"]) == 6
    assert body["daily"][0]["date"] == "2026-07-01"


@pytest.mark.parametrize("index", [0, 13, 25])
def test_navigation_uses_the_full_queue_with_exact_string_neighbors(api_client, index):
    ordered = api_client.get("/api/clients?limit=1000").json()["items"]
    gid = ordered[index]["gid"]
    # Pagination from a list request must never limit card navigation.
    response = api_client.get(f"/api/clients/{gid}/navigation?limit=1&offset=50")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "position": index + 1,
        "total": len(ordered),
        "previous_gid": ordered[index - 1]["gid"] if index > 0 else None,
        "next_gid": ordered[index + 1]["gid"] if index + 1 < len(ordered) else None,
    }
    for neighbor in (body["previous_gid"], body["next_gid"]):
        assert neighbor is None or (isinstance(neighbor, str) and int(neighbor) > 2**53)


def test_navigation_respects_filtered_order_and_reports_clients_outside_it(api_client, valid_frames):
    ordered = api_client.get("/api/clients?roles=transit").json()["items"]
    assert len(ordered) == 2
    for index, row in enumerate(ordered):
        response = api_client.get(f"/api/clients/{row['gid']}/navigation?roles=transit")
        assert response.status_code == 200
        assert response.json() == {
            "position": index + 1, "total": 2,
            "previous_gid": ordered[0]["gid"] if index == 1 else None,
            "next_gid": ordered[1]["gid"] if index == 0 else None,
        }
    isolated_gid = str(valid_frames[1].gid.iloc[-2])
    assert int(float(isolated_gid)) != int(isolated_gid)
    outside = api_client.get(f"/api/clients/{isolated_gid}/navigation?roles=transit")
    assert outside.status_code == 200
    assert outside.json() == {"position": None, "total": 2, "previous_gid": None, "next_gid": None}
    empty = api_client.get(f"/api/clients/{isolated_gid}/navigation?roles=terminal&seed=seed")
    assert empty.json() == {"position": None, "total": 0, "previous_gid": None, "next_gid": None}


def test_navigation_distinguishes_unknown_gid_from_a_client_outside_filter(api_client, valid_frames):
    assert api_client.get("/api/clients/9999999999999999999/navigation").status_code == 404
    assert api_client.get("/api/clients/not-a-number/navigation").status_code == 422
    gid = str(valid_frames[1].gid.iloc[0])
    assert api_client.get(f"/api/clients/{gid}/navigation?roles=unknown").status_code == 422


def test_ego_and_community_ignore_queue_filters_but_filtered_graph_obeys_them(api_client, valid_frames):
    _, nodes, _ = valid_frames
    gid = str(nodes.gid.iloc[1])
    all_nodes = api_client.get("/api/clients?limit=1000").json()["items"]
    community = next(row["cluster_id"] for row in all_nodes if row["gid"] == gid)
    expected_community = {row["gid"] for row in all_nodes if row["cluster_id"] == community}
    parameters = {"gid": gid, "roles": "terminal", "seed": "seed", "full": True}
    # This impossible queue filter must not remove the selected node's context.
    response = api_client.get("/api/graph", params=dict(parameters, mode="community"))
    assert response.status_code == 200
    assert graph_ids(response.text) == expected_community
    ego = api_client.get("/api/graph", params=dict(parameters, mode="ego", hops=1))
    assert graph_ids(ego.text) == {str(nodes.gid.iloc[index]) for index in [0, 1, 2, 5]}
    filtered = api_client.get("/api/graph", params=dict(parameters, mode="filtered"))
    assert graph_ids(filtered.text) == {gid}


@pytest.mark.parametrize("query", [
    "roles=unknown", "clusters=abc", "clusters=-1", "seed=maybe",
    "boundary=maybe", "limit=0", "limit=1001", "offset=-1",
])
def test_bad_queue_queries_have_validation_errors(api_client, query):
    assert api_client.get(f"/api/clients?{query}").status_code == 422


@pytest.mark.parametrize("query", ["hops=3", "mode=unknown", "color_by=unknown"])
def test_bad_graph_queries_have_validation_errors(api_client, valid_frames, query):
    gid = str(valid_frames[1].gid.iloc[0])
    assert api_client.get(f"/api/graph?gid={gid}&{query}").status_code == 422


def test_unknown_client_and_export_are_not_found(api_client):
    assert api_client.get("/api/clients/9999999999999999999").status_code == 404
    assert api_client.get("/api/clients/not-a-number").status_code == 422
    assert api_client.get("/api/graph?gid=9999999999999999999").status_code == 404
    assert api_client.get("/api/exports/config.json").status_code == 404
    assert api_client.get("/api/exports/..%2Fconfig.json").status_code == 404


@pytest.mark.parametrize("filename,expected_rows", [
    ("nodes_roles.csv", 26), ("top_nodes.csv", 20), ("clusters.csv", None),
])
def test_csv_downloads_keep_ids_exact_and_do_not_write_files(api_client, filename, expected_rows, tmp_path):
    response = api_client.get(f"/api/exports/{filename}")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert f'filename="{filename}"' in response.headers["content-disposition"]
    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    if expected_rows is not None:
        assert len(rows) == expected_rows
        assert all("." not in row["gid"] and int(row["gid"]) > 2**53 for row in rows)
    else:
        assert sum(int(row["n_nodes"]) for row in rows) == 26
    assert not (tmp_path / "output-not-written-by-get").exists()
    metadata = api_client.get("/api/exports/run_metadata.json")
    assert metadata.status_code == 200
    assert metadata.json()["data_summary"]["n_nodes"] == 26


def test_cache_is_reused_and_config_content_invalidates_it(api_client, tmp_path, monkeypatch):
    calls = []
    original = api.run_analysis

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "run_analysis", counted)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"community_resolution": 1.0}), encoding="utf-8")
    monkeypatch.setenv("MONEY_GRAPH_CONFIG", str(path))
    first = api_client.get("/api/health").json()["fingerprint"]
    assert api_client.get("/api/overview").status_code == 200
    assert api_client.get("/api/clients").status_code == 200
    assert len(calls) == 1
    path.write_text(json.dumps({"community_resolution": 1.2}), encoding="utf-8")
    second = api_client.get("/api/health").json()["fingerprint"]
    assert first != second
    assert len(calls) == 2


def test_concurrent_first_requests_share_one_analysis(api_client, monkeypatch):
    calls = []
    original = api.run_analysis

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "run_analysis", counted)
    cache = api_client.app.state.analysis_cache
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.get(), range(4)))
    assert len(calls) == 1
    assert all(result is results[0] for result in results)


def test_missing_data_returns_actionable_503_instead_of_stale_cache(api_client, data_dir):
    assert api_client.get("/api/health").status_code == 200
    (data_dir / "nodes.parquet").unlink()
    response = api_client.get("/api/overview")
    assert response.status_code == 503
    assert "MONEY_GRAPH_DATA" in response.json()["detail"]
    assert "nodes.parquet" in response.json()["detail"]
    assert api_client.get("/api/health").status_code == 503


def test_cors_allows_local_frontend_and_api_has_no_mutation_methods(api_client):
    response = api_client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    other_origin = api_client.get("/api/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in other_origin.headers
    assert api_client.post("/api/clients", json={}).status_code == 405
    preflight = api_client.options("/api/clients", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE",
    })
    assert preflight.status_code == 400
