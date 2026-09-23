"""The single analysis entry point used by the CLI and the dashboard."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
from time import perf_counter

import networkx as nx
import pandas as pd

from . import __version__
from .communities import aggregate_clusters, detect_communities
from .config import load_config, merge_config, validate_config
from .data import load_data, validate_data
from .features import build_graph, compute_features
from .ranking import rank_nodes
from .roles import assign_roles


@dataclass
class AnalysisResult:
    nodes: pd.DataFrame
    clusters: pd.DataFrame
    top_nodes: pd.DataFrame
    edges: pd.DataFrame
    transactions: pd.DataFrame
    graph: nx.DiGraph
    metadata: dict


def input_fingerprint(data_dir: str | Path, config: dict) -> str:
    digest = sha256()
    digest.update(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for name in ("nodes.parquet", "edges.parquet", "transactions.parquet"):
        digest.update(name.encode())
        digest.update((Path(data_dir) / name).read_bytes())
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(__version__.encode())
    return digest.hexdigest()


def run_analysis(data_dir: str | Path, config: dict | None = None) -> AnalysisResult:
    start = perf_counter()
    config = validate_config(merge_config(load_config(), config or {}))
    data_dir = Path(data_dir)
    edges, raw_nodes, tx = load_data(data_dir)
    summary = validate_data(edges, raw_nodes, tx, config)
    graph = build_graph(edges, raw_nodes)
    timings = {"load_validate_seconds": perf_counter() - start}
    tick = perf_counter()
    nodes = compute_features(graph, raw_nodes, tx, config)
    timings["features_seconds"] = perf_counter() - tick
    tick = perf_counter()
    communities = detect_communities(graph, config)
    nodes["cluster_id"] = nodes.gid.map(communities).astype(int)
    neighbours = {}
    for gid in graph:
        foreign = {communities[n] for n in set(graph.predecessors(gid)) | set(graph.successors(gid)) if communities[n] != communities[gid]}
        neighbours[gid] = len(foreign)
    nodes["intercluster_degree"] = nodes.gid.map(neighbours).astype(int)
    timings["communities_seconds"] = perf_counter() - tick
    tick = perf_counter()
    nodes = assign_roles(nodes, config)
    role_parameters = nodes.attrs.get("role_parameters", {})
    nodes, top_nodes, normalization = rank_nodes(nodes, config)
    nodes = nodes.sort_values("gid", kind="stable").reset_index(drop=True)
    clusters = aggregate_clusters(nodes, edges)
    timings["roles_ranking_seconds"] = perf_counter() - tick
    summary["n_weak_components"] = nx.number_weakly_connected_components(graph)
    summary["n_weak_components_with_edges"] = sum(1 for group in nx.weakly_connected_components(graph) if graph.subgraph(group).number_of_edges())
    summary["n_truncated"] = int(nodes.truncated_by_depth.sum())
    summary["n_excess_outflow"] = int(nodes.out_kzt.gt(nodes.in_kzt).sum())
    summary["n_excess_outflow_with_incoming"] = int((nodes.out_kzt.gt(nodes.in_kzt) & nodes.in_kzt.gt(0)).sum())
    summary["n_outflow_without_incoming"] = int((nodes.out_kzt.gt(0) & nodes.in_kzt.eq(0)).sum())
    metadata = {
        "version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
        "input_fingerprint": input_fingerprint(data_dir, config),
        "input_sha256": {name: sha256((data_dir / name).read_bytes()).hexdigest() for name in ("edges.parquet", "nodes.parquet", "transactions.parquet")},
        "data_summary": summary, "config": config, "normalization": normalization,
        "role_parameters": role_parameters, "role_counts": {str(k): int(v) for k, v in nodes.role.value_counts().sort_index().items()},
        "n_clusters": len(clusters),
        "betweenness": {"directed": True, "weight": None, "sample_size": min(config["betweenness_samples"], len(graph)), "random_seed": config["random_seed"]},
        "temporal": {"method": "FIFO, earlier calendar days only, each amount consumed once", "window_days": config["temporal_window_days"], "n_candidates": int(nodes.temporal_matched_kzt.gt(0).sum())},
        "dependencies": {name: version(name) for name in ("pandas", "numpy", "pyarrow", "networkx", "scipy")},
        "limitations": ["Только внутрибанковские переводы от 5000 KZT за июль 2026; исходящие от seed, до 4 переходов.", "Роли и приоритет — объяснимые гипотезы, не доказательство нарушения или вероятности виновности.", "За границей выгрузки продолжение неизвестно; входящие и остатки наблюдаются неполно.", "Даты не определяют порядок операций внутри дня; FIFO показывает временную совместимость."],
    }
    timings["total_seconds"] = perf_counter() - start
    metadata["timings"] = timings
    result = AnalysisResult(nodes, clusters, top_nodes, edges, tx, graph, metadata)
    from .export import validate_outputs
    validate_outputs(result)
    return result
