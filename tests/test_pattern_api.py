"""Evidence, versioning, graph amounts and offline report contracts."""

from copy import deepcopy
import json
from pathlib import Path
import re

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from money_graph.api import create_app
from money_graph.analyst_report import build_report, render_report_html
from money_graph.event_data import build_event_index


A, B, C, D, E, ISOLATE = [2**53 + 1 + n for n in range(6)]


@pytest.fixture
def pattern_client(tmp_path, monkeypatch):
    transfers = [(A, B, f"2026-07-{day:02}", 10_000.0) for day in [1, 3, 5, 6, 7, 8]]
    transfers += [(A, B, "2026-07-10", 10_000.0)] * 6
    transfers += [(src, B, f"2026-07-{day:02}", 5_000.0) for src in [D, E] for day in [1, 3]]
    transfers += [(B, C, "2026-07-02", 9_000.0), (B, C, "2026-07-04", 9_000.0), (C, A, "2026-07-03", 8_000.0)]
    tx = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx.date)
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
    edges["depth"] = 1
    nodes = pd.DataFrame({"gid": [A, B, C, D, E, ISOLATE], "depth": [0, 1, 2, 0, 0, 1], "is_seed": [True, False, False, True, True, False]})
    for name, frame in [("nodes", nodes), ("edges", edges), ("transactions", tx)]:
        frame.to_parquet(tmp_path / f"{name}.parquet", index=False)
    monkeypatch.setenv("MONEY_GRAPH_DATA", str(tmp_path))
    monkeypatch.setenv("MONEY_GRAPH_CONFIG", str(Path(__file__).resolve().parents[1] / "config.json"))
    with TestClient(create_app()) as client:
        yield client


def test_events_count_filters_and_ids(pattern_client):
    response = pattern_client.get(f"/api/clients/{B}/patterns")
    assert response.status_code == 200
    payload = response.json()
    assert payload["coverage"]["search_complete"]
    assert payload["status"]["in_burst_history_sufficient"]
    assert payload["total"] == sum(payload["kind_counts"].values())
    assert {"burst", "repeated_group", "repeated_route", "cycle", "transit_window"} <= set(payload["kind_counts"])
    assert all(str(B) in event["gids"] for event in payload["items"])
    assert all(isinstance(gid, str) for event in payload["items"] for gid in event["gids"])
    single = pattern_client.get(f"/api/clients/{B}/patterns?kind=burst&limit=1").json()
    assert single["total"] == payload["kind_counts"]["burst"]
    assert all(event["kind"] == "burst" for event in single["items"])
    filtered = pattern_client.get("/api/clients?has_patterns=true").json()["items"]
    assert str(ISOLATE) not in {node["gid"] for node in filtered}
    assert all(node["pattern_count"] > 0 for node in filtered)


def test_burst_source_and_comparison_operations_are_separate_and_reconciled(pattern_client):
    listing = pattern_client.get(f"/api/clients/{B}/patterns?kind=burst").json()
    event = listing["items"][0]
    detail = pattern_client.get(f'/api/patterns/{event["pattern_id"]}', params={"fingerprint": listing["fingerprint"]}).json()
    assert len(detail["transactions"]) == 6  # Identical rows are genuine separate observations.
    assert len({row["ref"] for row in detail["transactions"]}) == 6
    assert sum(row["sum_kzt"] for row in detail["transactions"]) == event["measurements"]["sum_kzt"]
    comparisons = {row["ref"]: row for row in detail["comparison_transactions"]}
    for day in event["measurements"]["baseline"]:
        assert sum(comparisons[ref]["sum_kzt"] for ref in day["evidence_refs"]) == day["sum_kzt"]
    assert set(comparisons).isdisjoint(event["evidence_refs"])


@pytest.mark.parametrize("target", ["detail", "graph", "report", "list"])
def test_old_snapshot_is_rejected(pattern_client, target):
    event = pattern_client.get(f"/api/clients/{B}/patterns").json()["items"][0]
    urls = {"detail": f'/api/patterns/{event["pattern_id"]}', "graph": '/api/graph',
            "report": f"/api/clients/{B}/report", "list": f"/api/clients/{B}/patterns"}
    params = {"fingerprint": "old"}
    if target == "graph":
        params.update(gid=str(B), pattern_id=event["pattern_id"])
    assert pattern_client.get(urls[target], params=params).status_code == 409


def test_event_graph_has_only_evidence_edges_and_event_amounts(pattern_client):
    listing = pattern_client.get(f"/api/clients/{B}/patterns?kind=burst").json()
    event = listing["items"][0]
    response = pattern_client.get("/api/graph", params={"gid": str(B), "pattern_id": event["pattern_id"], "fingerprint": listing["fingerprint"], "roles": "unknown-role"})
    assert response.status_code == 422
    response = pattern_client.get("/api/graph", params={"gid": str(B), "pattern_id": event["pattern_id"], "fingerprint": listing["fingerprint"], "roles": "terminal"})
    assert response.status_code == 200
    edge_json = re.search(r"edges:new vis.DataSet\(", response.text)
    edges, _ = json.JSONDecoder().raw_decode(response.text[edge_json.end():])
    assert {(edge["from"], edge["to"]) for edge in edges} == {(str(A), str(B))}
    assert "60,000.00 KZT" in edges[0]["title"]
    assert "6 операций" in edges[0]["title"]
    assert "Операции выбранного события" in edges[0]["title"]
    assert pattern_client.get("/api/graph", params={"gid": str(ISOLATE), "pattern_id": event["pattern_id"]}).status_code == 422


def test_report_is_evidence_backed_offline_and_preserves_ids(pattern_client):
    payload = pattern_client.get(f"/api/clients/{B}/report?format=json").json()
    assert payload["client"]["gid"] == str(B)
    assert payload["patterns"] and payload["recommendations"]
    refs = {row["ref"] for row in payload["evidence"]}
    assert all(set(event["evidence_refs"]) <= refs for event in payload["patterns"])
    assert all(ref in refs for event in payload["patterns"] for day in event["measurements"].get("baseline", []) for ref in day["evidence_refs"])
    html = pattern_client.get(f"/api/clients/{B}/report?format=html")
    assert html.status_code == 200 and 'attachment;' in html.headers["content-disposition"]
    assert str(B) in html.text and payload["fingerprint"] in html.text
    assert "Что проверить дальше" in html.text and "window.print()" in html.text
    assert not re.search(r'(src|href)=["\']https?://', html.text)
    assert "Альтернатива: none" not in html.text


def test_report_escapes_text_and_discloses_incomplete_search(pattern_client):
    result = pattern_client.app.state.analysis_cache.get()
    brief = deepcopy(build_report(result, str(B)))
    brief["patterns"][0]["summary"] = '<script>alert("x")</script>'
    brief["coverage"]["search_complete"] = False
    html = render_report_html(brief)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "Список событий неполный" in html


def test_empty_patterns_and_bad_requests_are_explicit(pattern_client):
    payload = pattern_client.get(f"/api/clients/{ISOLATE}/patterns").json()
    assert payload["items"] == [] and payload["total"] == 0
    assert not payload["status"]["burst_history_sufficient"]
    assert "недостаточно" in " ".join(payload["status"]["notes"]).lower()
    assert pattern_client.get("/api/patterns/missing").status_code == 404
    assert pattern_client.get("/api/clients/999999/report").status_code == 404
    assert pattern_client.get(f"/api/clients/{B}/patterns?limit=0").status_code == 422
    assert pattern_client.get(f"/api/clients/{B}/patterns?kind=unknown").status_code == 422
    assert pattern_client.get(f"/api/clients/{B}/report?format=exe").status_code == 422


def test_evidence_index_is_order_independent_and_keeps_duplicates():
    frame = pd.DataFrame([(A, B, "2026-07-01", 5000.0)] * 2 + [(B, C, "2026-07-02", 7000.0)], columns=["src", "dst", "date", "sum_kzt"])
    first = build_event_index(frame)
    shuffled = build_event_index(frame.iloc[::-1])
    assert first == shuffled
    assert len(first.rows) == 3
    assert first.edge_days[(str(A), str(B), "2026-07-01")]["n_tx"] == 2
