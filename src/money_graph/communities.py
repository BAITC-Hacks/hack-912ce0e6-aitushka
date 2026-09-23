"""Deterministic weighted Louvain and directed community summaries."""

from __future__ import annotations

from collections.abc import Mapping

import networkx as nx
import pandas as pd

from .explain import cluster_hypothesis


CLUSTER_COLUMNS = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]


def detect_communities(graph: nx.DiGraph, config: Mapping) -> dict[int, int]:
    """Reciprocal edge amounts are added explicitly, not silently overwritten."""
    projection = nx.Graph()
    projection.add_nodes_from(sorted(graph.nodes))
    for source, target, attributes in sorted(graph.edges(data=True), key=lambda e: (e[0], e[1])):
        weight = float(attributes.get("sum_kzt", attributes.get("weight", 0.0)))
        if weight <= 0:
            continue
        old_weight = projection.get_edge_data(source, target, {}).get("weight", 0.0)
        projection.add_edge(source, target, weight=old_weight + weight)
    isolated = set(nx.isolates(projection))
    groups = [{gid} for gid in sorted(isolated)]
    active = projection.subgraph(sorted(set(projection.nodes) - isolated)).copy()
    if active.number_of_edges():
        groups.extend(nx.community.louvain_communities(
            active,
            weight="weight",
            resolution=float(config.get("community_resolution", 1.0)),
            seed=int(config.get("random_seed", 42)),
        ))
    ordered = sorted((sorted(group) for group in groups), key=lambda group: (group[0], len(group)))
    return {int(gid): cluster_id for cluster_id, group in enumerate(ordered) for gid in group}


def aggregate_clusters(nodes_df: pd.DataFrame, edges_df: pd.DataFrame) -> pd.DataFrame:
    """Internal turnover counts each original directed edge exactly once."""
    if nodes_df.empty:
        return pd.DataFrame(columns=CLUSTER_COLUMNS)
    membership = nodes_df.set_index("gid")["cluster_id"]
    sources = edges_df["src"].map(membership)
    targets = edges_df["dst"].map(membership)
    internal_mask = sources.eq(targets) & sources.notna()
    internal = edges_df.loc[internal_mask, "sum_kzt"].groupby(sources.loc[internal_mask]).sum()
    rows = []
    for cluster_id, members in nodes_df.groupby("cluster_id", sort=True):
        ordering = ["priority_score", "gid"] if "priority_score" in members else ["gid"]
        ascending = [False, True] if len(ordering) == 2 else [True]
        top = members.sort_values(ordering, ascending=ascending, kind="stable").head(5)
        n_nodes, n_seed = len(members), int(members["is_seed"].sum())
        turnover = float(internal.get(cluster_id, 0.0))
        counts = members["role"].value_counts().to_dict() if "role" in members else {}
        rows.append({
            "cluster_id": int(cluster_id),
            "n_nodes": n_nodes,
            "n_seed": n_seed,
            "sum_kzt_internal": turnover,
            "top_gids": ";".join(str(int(gid)) for gid in top["gid"]),
            "hypothesis": cluster_hypothesis(n_nodes, n_seed, counts, turnover),
        })
    return pd.DataFrame(rows, columns=CLUSTER_COLUMNS)
