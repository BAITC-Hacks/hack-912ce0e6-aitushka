"""Read and validate the supplied tables without dropping observations."""

from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_integer_dtype, is_numeric_dtype


class DataValidationError(ValueError):
    """The dataset cannot safely be analysed."""


SCHEMAS = {
    "edges": ("src", "dst", "sum_kzt", "n_tx", "depth"),
    "nodes": ("gid", "depth", "is_seed"),
    "transactions": ("src", "dst", "date", "sum_kzt"),
}


def load_data(data_dir: str | Path):
    data_dir = Path(data_dir)
    tables = []
    for name in ("edges", "nodes", "transactions"):
        path = data_dir / f"{name}.parquet"
        if not path.is_file():
            raise DataValidationError(f"Не найден файл: {path}")
        try:
            table = pd.read_parquet(path)
        except Exception as exc:
            raise DataValidationError(f"Не удалось прочитать {path.name}: {exc}") from exc
        missing = set(SCHEMAS[name]) - set(table.columns)
        if missing:
            raise DataValidationError(f"{name}: отсутствуют обязательные колонки {sorted(missing)}")
        tables.append(table)
    edges, nodes, tx = tables
    try:
        tx["date"] = pd.to_datetime(tx["date"], errors="raise").dt.normalize()
    except (ValueError, TypeError, AttributeError) as exc:
        raise DataValidationError(f"Некорректные даты операций: {exc}") from exc
    return edges, nodes, tx


def validate_data(edges, nodes, tx, config):
    tables = {"edges": edges, "nodes": nodes, "transactions": tx}
    for name, table in tables.items():
        missing = set(SCHEMAS[name]) - set(table.columns)
        if missing:
            raise DataValidationError(f"{name}: отсутствуют колонки {sorted(missing)}")
        if table[list(SCHEMAS[name])].isna().any().any():
            raise DataValidationError(f"{name}: пустые значения в обязательных колонках")
        int_columns = ("gid", "depth") if name == "nodes" else (("src", "dst", "n_tx", "depth") if name == "edges" else ("src", "dst"))
        for column in int_columns:
            if not is_integer_dtype(table[column]) or is_bool_dtype(table[column]):
                raise DataValidationError(f"{name}.{column}: ожидаются целые числа без потери точности")
    if nodes.empty:
        raise DataValidationError("Таблица nodes пуста")
    if nodes.gid.duplicated().any():
        raise DataValidationError("nodes: дублирующиеся gid")
    if not is_bool_dtype(nodes.is_seed):
        raise DataValidationError("nodes.is_seed должен иметь тип bool")
    if not nodes.depth.between(0, config["max_depth"]).all():
        raise DataValidationError("nodes.depth выходит за границы глубины обхода")
    if not (nodes.is_seed == nodes.depth.eq(0)).all():
        raise DataValidationError("is_seed и depth=0 не согласованы")
    if not edges.depth.between(1, config["max_depth"]).all():
        raise DataValidationError("edges.depth выходит за границы обхода")
    if edges.duplicated(["src", "dst"]).any():
        raise DataValidationError("edges: дублирующиеся пары src/dst")
    if not (edges.n_tx > 0).all():
        raise DataValidationError("edges.n_tx должен быть положительным")
    known = set(nodes.gid)
    for name, table in (("edges", edges), ("transactions", tx)):
        unknown = (set(table.src) | set(table.dst)) - known
        if unknown:
            raise DataValidationError(f"{name}: неизвестные узлы (нет в nodes): {sorted(unknown)[:5]}")
        if not is_numeric_dtype(table.sum_kzt) or not np.isfinite(table.sum_kzt.astype(float)).all() or not (table.sum_kzt > 0).all():
            raise DataValidationError(f"{name}.sum_kzt: нужны конечные положительные суммы")
    try:
        dates = pd.to_datetime(tx.date, errors="raise")
    except (TypeError, ValueError) as exc:
        raise DataValidationError("transactions.date: некорректная дата") from exc
    if dates.isna().any():
        raise DataValidationError("transactions.date: пустая дата")
    agg = tx.groupby(["src", "dst"], sort=True).agg(tx_sum=("sum_kzt", "sum"), tx_count=("sum_kzt", "size")).reset_index()
    compare = edges.merge(agg, on=["src", "dst"], how="outer", indicator=True)
    if not compare["_merge"].eq("both").all():
        raise DataValidationError("edges и transactions: не совпадают пары переводов")
    if not np.isclose(compare.sum_kzt, compare.tx_sum, atol=config["money_tolerance"], rtol=0).all():
        raise DataValidationError("edges и transactions: не совпадают суммы (sum_kzt)")
    if not compare.n_tx.eq(compare.tx_count).all():
        raise DataValidationError("edges и transactions: не совпадает количество операций (n_tx)")
    isolated = known - (set(edges.src) | set(edges.dst))
    return {
        "n_nodes": len(nodes), "n_edges": len(edges), "n_transactions": len(tx),
        "n_seed": int(nodes.is_seed.sum()), "n_isolates": len(isolated),
        "sum_kzt": float(edges.sum_kzt.sum()),
        "date_min": str(dates.min().date()) if len(tx) else None,
        "date_max": str(dates.max().date()) if len(tx) else None,
        "depth_counts": {str(k): int(v) for k, v in nodes.depth.value_counts().sort_index().items()},
        "self_loop_edges": int(edges.src.eq(edges.dst).sum()),
        "identical_transaction_rows": int(tx.duplicated().sum()),
    }

