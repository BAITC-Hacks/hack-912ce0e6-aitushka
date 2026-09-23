"""Four-component inspection priority; it is not a probability of wrongdoing."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from .explain import priority_explanation


DEFAULT_WEIGHTS = {"seed_convergence": 0.35, "observed_flow": 0.25, "brokerage": 0.25, "fan_in": 0.15}


def normalize_positive(values: pd.Series, quantile: float = 0.95) -> tuple[pd.Series, float]:
    """N(x)=min(max(x,0)/Qq(x>0),1), with zero result for an empty positive tail."""
    if not 0 < quantile <= 1:
        raise ValueError("normalization_quantile must be in (0, 1].")
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0).astype(float)
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Cannot normalize non-finite feature values.")
    if (numeric < 0).any():
        raise ValueError("Priority features must be non-negative.")
    positive = numeric[numeric > 0]
    scale = float(positive.quantile(quantile)) if len(positive) else 0.0
    normalized = (numeric / scale).clip(0.0, 1.0) if scale > 0 else numeric * 0.0
    return normalized, scale


def rank_nodes(df: pd.DataFrame, config: Mapping) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    result = df.copy()
    weights = dict(config.get("priority_weights", DEFAULT_WEIGHTS))
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError(f"priority_weights must contain exactly {list(DEFAULT_WEIGHTS)}.")
    values = np.asarray(list(weights.values()), dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or not np.isclose(values.sum(), 1.0, atol=1e-9, rtol=0):
        raise ValueError("Priority weights must be finite, non-negative and sum to 1.")
    quantile = float(config.get("normalization_quantile", 0.95))
    features = {
        "seed_convergence": result["seed_reach"],
        "observed_flow": result[["in_kzt", "out_kzt"]].max(axis=1),
        "brokerage": result["betweenness"],
        "fan_in": result["in_deg"],
    }
    metadata = {"method": "min(x / quantile_positive, 1); zero preserved", "quantile": quantile, "scales": {}, "weights": weights}
    result["priority_score"] = 0.0
    for name, feature in features.items():
        normalized, scale = normalize_positive(feature, quantile)
        result[f"norm_{name}"] = normalized
        result[f"contribution_{name}"] = normalized * float(weights[name])
        result["priority_score"] += result[f"contribution_{name}"]
        metadata["scales"][name] = scale
    result["priority_score"] = result["priority_score"].clip(0.0, 1.0)
    result["why"] = result.apply(priority_explanation, axis=1) if len(result) else pd.Series(dtype=str)
    top_n = int(config.get("top_n", 20))
    if top_n < 20:
        raise ValueError("top_n must be at least 20 for the submission contract.")
    top = result.sort_values(["priority_score", "gid"], ascending=[False, True], kind="stable").head(top_n).copy()
    top.insert(0, "rank", np.arange(1, len(top) + 1))
    top = top[["rank", "gid", "role", "priority_score", "why"]].reset_index(drop=True)
    return result, top, metadata
