"""Validate the external contract before writing any output."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from .config import ROLES

NODE_COLUMNS = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
CLUSTER_COLUMNS = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
TOP_COLUMNS = ["rank", "gid", "role", "priority_score", "why"]


def validate_outputs(result):
    nodes, clusters, top = result.nodes, result.clusters, result.top_nodes
    for name, frame, required in (("nodes_roles", nodes, NODE_COLUMNS), ("clusters", clusters, CLUSTER_COLUMNS), ("top_nodes", top, TOP_COLUMNS)):
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(f"{name}: отсутствуют обязательные колонки {sorted(missing)}")
        if frame[required].isna().any().any():
            raise ValueError(f"{name}: пустые обязательные поля")
    if nodes.gid.duplicated().any() or set(nodes.gid) != set(result.graph.nodes):
        raise ValueError("Выгрузка должна содержать каждый узел графа ровно один раз")
    if not nodes.role.isin(ROLES).all():
        raise ValueError("Недопустимая роль")
    for column in ("role_score", "priority_score"):
        if not np.isfinite(nodes[column]).all() or not nodes[column].between(0, 1).all():
            raise ValueError(f"{column}: значения должны быть конечными в [0, 1]")
    evidence = nodes.evidence.astype(str)
    if not evidence.str.len().between(1, 200).all() or not evidence.str.contains(r"\d").all():
        raise ValueError("evidence должен содержать числа и от 1 до 200 символов")
    if clusters.cluster_id.duplicated().any() or set(nodes.cluster_id) != set(clusters.cluster_id):
        raise ValueError("Некорректное покрытие кластеров")
    actual_sizes = nodes.groupby("cluster_id").size().sort_index()
    saved_sizes = clusters.set_index("cluster_id").n_nodes.sort_index()
    if not np.array_equal(actual_sizes.values, saved_sizes.values):
        raise ValueError("Размеры кластеров не совпадают с узлами")
    if not clusters.hypothesis.astype(str).str.strip().str.len().gt(0).all():
        raise ValueError("Нужна гипотеза для каждого кластера")
    if len(top) < min(20, len(nodes)) or top.gid.duplicated().any():
        raise ValueError("Топ должен содержать не менее 20 уникальных узлов (либо все узлы малого тестового графа)")
    if not top["rank"].tolist() == list(range(1, len(top) + 1)):
        raise ValueError("Некорректный порядок rank")
    expected = nodes.sort_values(["priority_score", "gid"], ascending=[False, True], kind="stable").head(len(top))
    if top.gid.tolist() != expected.gid.tolist() or not np.allclose(top.priority_score, expected.priority_score, atol=1e-12, rtol=0):
        raise ValueError("Топ не согласован с приоритетами узлов")
    if top.role.tolist() != expected.role.tolist() or not top.why.astype(str).str.strip().str.len().gt(0).all():
        raise ValueError("Топ содержит неверные роли или пустые объяснения")


def export_result(result, out_dir: str | Path):
    validate_outputs(result)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # All four files are prepared first. Manifest is replaced last.
    with TemporaryDirectory(prefix=".export-", dir=out_dir) as stage:
        stage = Path(stage)
        columns = NODE_COLUMNS + [column for column in result.nodes.columns if column not in NODE_COLUMNS]
        result.nodes[columns].to_csv(stage / "nodes_roles.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
        result.clusters[CLUSTER_COLUMNS].to_csv(stage / "clusters.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
        result.top_nodes[TOP_COLUMNS].to_csv(stage / "top_nodes.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
        (stage / "run_metadata.json").write_text(json.dumps(result.metadata, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "run_metadata.json"):
            os.replace(stage / name, out_dir / name)

