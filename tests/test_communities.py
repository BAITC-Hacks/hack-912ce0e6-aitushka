import networkx as nx
import pandas as pd
import pytest

from money_graph.communities import aggregate_clusters, detect_communities


def test_communities_preserve_isolates_and_disconnected_components(config):
    graph = nx.DiGraph()
    graph.add_nodes_from([10, 11, 20, 21, 99])
    graph.add_edge(10, 11, sum_kzt=100)
    graph.add_edge(11, 10, sum_kzt=200)
    graph.add_edge(20, 21, sum_kzt=50)
    groups = detect_communities(graph, config)
    assert set(groups) == set(graph)
    assert groups[10] == groups[11]
    assert groups[20] == groups[21]
    assert len({groups[10], groups[20], groups[99]}) == 3
    reversed_graph = nx.DiGraph()
    reversed_graph.add_nodes_from(reversed(list(graph.nodes)))
    reversed_graph.add_edges_from(reversed(list(graph.edges(data=True))))
    assert detect_communities(reversed_graph, config) == groups


def test_cluster_internal_turnover_counts_both_directions_once_and_excludes_external_edges():
    nodes = pd.DataFrame({
        "gid": [10, 11, 20, 99],
        "cluster_id": [0, 0, 1, 2],
        "is_seed": [True, False, False, False],
        "priority_score": [0.1, 0.9, 0.2, 0.0],
        "role": ["peripheral", "transit", "terminal", "peripheral"],
    })
    edges = pd.DataFrame({
        "src": [10, 11, 11],
        "dst": [11, 10, 20],
        "sum_kzt": [100.0, 200.0, 400.0],
    })
    clusters = aggregate_clusters(nodes, edges).set_index("cluster_id")
    assert clusters.loc[0, "sum_kzt_internal"] == pytest.approx(300.0)
    assert clusters.loc[1, "sum_kzt_internal"] == 0
    assert clusters.loc[2, "sum_kzt_internal"] == 0
    assert clusters["n_nodes"].sum() == 4
    assert clusters.loc[0, "n_seed"] == 1
    assert clusters.loc[0, "top_gids"].split(";") == ["11", "10"]
    assert clusters.loc[2, "n_nodes"] == 1
