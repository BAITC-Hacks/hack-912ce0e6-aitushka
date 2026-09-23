"""Validated configuration shared by the command line and UI."""

from copy import deepcopy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral")


def validate_config(config: dict) -> dict:
    for name in ("random_seed", "top_n", "max_depth", "betweenness_samples", "temporal_window_days"):
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == "random_seed" else 1):
            raise ValueError(f"Параметр {name}: требуется положительное целое число (seed может быть 0)")
    if config["top_n"] < 20:
        raise ValueError("top_n должен быть не меньше 20")
    for name in ("community_resolution", "money_tolerance"):
        if not math.isfinite(config[name]) or config[name] <= 0:
            raise ValueError(f"{name} должен быть конечным положительным числом")
    if not 0 < config["normalization_quantile"] <= 1:
        raise ValueError("normalization_quantile должен быть в (0, 1]")
    expected = {"seed_convergence", "observed_flow", "brokerage", "fan_in"}
    weights = config["priority_weights"]
    if set(weights) != expected or any(not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError("Нужны четыре неотрицательных конечных веса приоритета")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("Сумма весов приоритета должна равняться 1")
    role = config["roles"]
    for name, value in role.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Некорректный порог роли: {name}")
    for name in ("consolidator_min_in", "consolidator_min_seed", "distributor_min_out", "coordinator_min_seed", "coordinator_min_communities"):
        if not isinstance(role[name], int):
            raise ValueError(f"{name} должен быть целым числом")
    if not 0 <= role["transit_min_ratio"] <= 1 <= role["transit_max_ratio"] or role["transit_min_ratio"] == role["transit_max_ratio"]:
        raise ValueError("Интервал транзита должен включать 1 и иметь ненулевую ширину")
    if not 0 < role["coordinator_quantile"] <= 1:
        raise ValueError("coordinator_quantile должен быть в (0, 1]")
    order = config["role_tie_order"]
    if len(order) != len(ROLES) or set(order) != set(ROLES):
        raise ValueError("role_tie_order должен содержать все шесть ролей без повторов")
    return config


def load_config(path: str | Path | None = None) -> dict:
    with (ROOT / "config.json").open(encoding="utf-8") as stream:
        config = json.load(stream)
    if path is not None and Path(path).resolve() != (ROOT / "config.json").resolve():
        with Path(path).open(encoding="utf-8") as stream:
            override = json.load(stream)
        config = merge_config(config, override)
    return validate_config(config)


def merge_config(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    unknown = set(override) - set(base)
    if unknown:
        raise ValueError(f"Неизвестные параметры: {sorted(unknown)}")
    for key, value in override.items():
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = value
    return result
