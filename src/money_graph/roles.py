"""Transparent multi-candidate role rules with explicit observation limits."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from .explain import observation_note, role_evidence
from .ranking import normalize_positive


ROLE_TIE_ORDER = ("coordinator", "consolidator", "distributor", "transit", "terminal", "peripheral")


def assign_roles(df: pd.DataFrame, config: Mapping) -> pd.DataFrame:
    result = df.copy()
    rules = config.get("roles", {})
    quantile = float(config.get("normalization_quantile", 0.95))
    min_in = int(rules.get("consolidator_min_in", 3))
    min_out = int(rules.get("distributor_min_out", 10))
    fan_threshold = float(rules.get("distributor_fan_ratio", 2.0))
    transit_low = float(rules.get("transit_min_ratio", 0.8))
    transit_high = float(rules.get("transit_max_ratio", 1.2))
    coordinator_q = float(rules.get("coordinator_quantile", 0.9))
    min_seeds = int(rules.get("coordinator_min_seed", 2))
    min_communities = int(rules.get("coordinator_min_communities", 2))
    consolidator_seeds = int(rules.get("consolidator_min_seed", 2))
    if min(min_in, min_out, min_seeds, min_communities, consolidator_seeds) < 1 or fan_threshold <= 0:
        raise ValueError("Role count thresholds and fan ratio must be positive.")
    if not 0 <= transit_low <= 1 <= transit_high or transit_low == transit_high:
        raise ValueError("Transit interval must include 1 and have positive width.")
    if not 0 < coordinator_q <= 1:
        raise ValueError("coordinator_quantile must be in (0, 1].")
    order = tuple(config.get("role_tie_order", ROLE_TIE_ORDER))
    if len(order) != len(ROLE_TIE_ORDER) or set(order) != set(ROLE_TIE_ORDER):
        raise ValueError("role_tie_order must contain each of the six roles once.")

    norms = {}
    scales = {}
    for feature in ("in_deg", "out_deg", "in_kzt", "seed_reach", "betweenness"):
        norms[feature], scales[feature] = normalize_positive(result[feature], quantile)
    positive_brokerage = result.loc[result["betweenness"] > 0, "betweenness"]
    coordinator_threshold = float(positive_brokerage.quantile(coordinator_q)) if len(positive_brokerage) else float("inf")
    boundary = result["truncated_by_depth"].astype(bool) | (result["depth"] >= int(config.get("max_depth", 4)))
    seed = result["is_seed"].astype(bool)
    in_positive = result["in_kzt"] > 0
    ratio = pd.to_numeric(result["pass_through"], errors="coerce")
    retention_eligible = (~boundary) & (~seed) & in_positive & ratio.notna()
    retention = pd.Series(0.0, index=result.index)
    retention.loc[retention_eligible] = (1.0 - ratio.loc[retention_eligible]).clip(0, 1)

    scores = pd.DataFrame(0.0, index=result.index, columns=order)
    consolidator = (result["in_deg"] >= min_in) & (result["seed_reach"] >= consolidator_seeds)
    scores.loc[consolidator, "consolidator"] = (
        0.50 + 0.20 * norms["in_deg"] + 0.15 * norms["seed_reach"] + 0.15 * retention
    ).loc[consolidator]

    fan_ratio = result["out_deg"] / result["in_deg"].clip(lower=1)
    distributor = (result["out_deg"] >= min_out) & (fan_ratio >= fan_threshold)
    scores.loc[distributor, "distributor"] = (
        0.50 + 0.30 * norms["out_deg"] + 0.20 * (fan_ratio / (2.0 * fan_threshold)).clip(0, 1)
    ).loc[distributor]

    transit = (~boundary) & (~seed) & in_positive & (result["out_kzt"] > 0) & ratio.between(transit_low, transit_high)
    closeness = (1.0 - (ratio - 1.0).abs() / max(1.0 - transit_low, transit_high - 1.0)).clip(0, 1).fillna(0)
    temporal = result["temporal_share"].fillna(0.0).clip(0, 1) if "temporal_share" in result else pd.Series(0.0, index=result.index)
    scores.loc[transit, "transit"] = (0.55 + 0.30 * closeness + 0.15 * temporal).loc[transit]

    terminal = (~boundary) & (~seed) & in_positive & (result["out_deg"] == 0)
    scores.loc[terminal, "terminal"] = (0.55 + 0.25 * norms["in_kzt"] + 0.20 * norms["in_deg"]).loc[terminal]

    coordinator = (
        (result["betweenness"] > 0)
        & (result["betweenness"] >= coordinator_threshold)
        & (result["intercluster_degree"] >= min_communities)
        & (result["seed_reach"] >= min_seeds)
    )
    scores.loc[coordinator, "coordinator"] = (
        0.55 + 0.25 * norms["betweenness"] + 0.10 * norms["seed_reach"]
        + 0.10 * (result["intercluster_degree"] / (2.0 * min_communities)).clip(0, 1)
    ).loc[coordinator]
    isolated = (result["in_deg"] + result["out_deg"]) == 0
    scores["peripheral"] = np.where(isolated, 0.05, 0.10)
    scores = scores.clip(0.0, 1.0)
    for role in order:
        result[f"score_{role}"] = scores[role]
    result["role"] = scores.idxmax(axis=1)
    result["role_score"] = scores.max(axis=1)

    alternatives, alternative_scores = [], []
    for record in scores.to_dict(orient="records"):
        candidates = sorted(
            (role for role in order if role != "peripheral" and record[role] > 0),
            key=lambda role: (-record[role], order.index(role)),
        )
        alternatives.append(candidates[1] if len(candidates) >= 2 else "none")
        alternative_scores.append(record[candidates[1]] if len(candidates) >= 2 else 0.0)
    result["alternative_role"] = alternatives
    result["alternative_role_score"] = alternative_scores
    if len(result):
        result["evidence"] = result.apply(lambda row: role_evidence(row, row["role"]), axis=1)
        result["observation_note"] = result.apply(observation_note, axis=1)
    else:
        result["evidence"] = pd.Series(dtype=str)
        result["observation_note"] = pd.Series(dtype=str)
    result.attrs["role_parameters"] = {
        "normalization_quantile": quantile,
        "normalization_scales": scales,
        "coordinator_threshold": coordinator_threshold if np.isfinite(coordinator_threshold) else None,
        "tie_order": list(order),
    }
    return result
