import pytest

from money_graph.features import build_graph, compute_features


def test_graph_keeps_isolates_and_exact_large_identities(valid_frames):
    edges, nodes, _ = valid_frames
    graph = build_graph(edges, nodes)
    assert set(graph.nodes) == set(nodes["gid"])
    assert set(graph.edges) == set(edges[["src", "dst"]].itertuples(index=False, name=None))
    isolated_gid = int(nodes["gid"].iloc[-1])
    assert graph.degree(isolated_gid) == 0
    assert graph.number_of_nodes() == 26
    assert graph.number_of_edges() == 5


def test_directional_flows_and_counts_remain_distinct(valid_frames, config):
    edges, nodes, transactions = valid_frames
    graph = build_graph(edges, nodes)
    features = compute_features(graph, nodes, transactions, config).set_index("gid")
    receiving_gid = int(nodes["gid"].iloc[1])
    row = features.loc[receiving_gid]
    assert row["in_deg"] == 1
    assert row["out_deg"] == 2
    assert row["in_kzt"] == pytest.approx(20_000)
    assert row["out_kzt"] == pytest.approx(17_500)
    assert row["in_tx"] == 2
    assert row["out_tx"] == 2
    assert row["pass_through"] == pytest.approx(0.875)
    isolated_gid = int(nodes["gid"].iloc[-1])
    assert features.loc[isolated_gid, "in_deg"] == 0
    assert features.loc[isolated_gid, "out_deg"] == 0
    assert len(features) == len(nodes)
