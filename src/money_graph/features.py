"""Directed structural and transaction features, including isolated nodes."""

import networkx as nx
import numpy as np

from .temporal import temporal_features


def build_graph(edges, nodes):
    graph = nx.DiGraph()
    for row in nodes.sort_values("gid").itertuples(index=False):
        graph.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    for row in edges.sort_values(["src", "dst"]).itertuples(index=False):
        graph.add_edge(int(row.src), int(row.dst), sum_kzt=float(row.sum_kzt), n_tx=int(row.n_tx), depth=int(row.depth))
    return graph


def compute_features(graph, nodes, tx, config):
    df = nodes[["gid", "depth", "is_seed"]].sort_values("gid").reset_index(drop=True).copy()
    for column, values in (
        ("in_deg", dict(graph.in_degree())), ("out_deg", dict(graph.out_degree())),
        ("in_kzt", dict(graph.in_degree(weight="sum_kzt"))), ("out_kzt", dict(graph.out_degree(weight="sum_kzt"))),
        ("in_tx", dict(graph.in_degree(weight="n_tx"))), ("out_tx", dict(graph.out_degree(weight="n_tx"))),
    ):
        df[column] = df.gid.map(values).fillna(0)
    pagerank = nx.pagerank(graph, weight="sum_kzt", max_iter=1000, tol=1e-10)
    k = min(len(graph), config["betweenness_samples"])
    centrality = nx.betweenness_centrality(graph, k=k if k < len(graph) else None, weight=None, seed=config["random_seed"])
    df["pagerank"] = df.gid.map(pagerank).fillna(0.0)
    df["betweenness"] = df.gid.map(centrality).fillna(0.0)
    df["pass_through"] = df.out_kzt / df.in_kzt.replace(0, np.nan)
    df["truncated_by_depth"] = df.depth.eq(config["max_depth"]) & df.out_deg.eq(0)
    seeds = nodes.loc[nodes.is_seed, "gid"].sort_values().tolist()
    seed_reach = dict.fromkeys(graph, 0)
    for seed in seeds:
        for target in nx.descendants(graph, int(seed)):
            seed_reach[target] += 1
    df["seed_reach"] = df.gid.map(seed_reach).astype(int)
    df["observed_flow"] = df[["in_kzt", "out_kzt"]].max(axis=1)
    df["incomplete_inflow"] = df.is_seed | df.in_kzt.eq(0) | df.out_kzt.gt(df.in_kzt)
    return df.merge(temporal_features(df, tx, config["temporal_window_days"]), on="gid", validate="one_to_one")
