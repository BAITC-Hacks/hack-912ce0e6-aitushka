"""Small, reconciled networks that exercise the data and output contracts."""

from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def config():
    from money_graph.config import load_config

    return load_config()


@pytest.fixture
def valid_frames():
    """Include adjacent int64 identities beyond JavaScript's exact integer range."""
    gids = [2**53 + 1 + index for index in range(26)]
    depths = [0, 1, 2, 3, 4, 2] + [1] * 20
    nodes = pd.DataFrame(
        {"gid": gids, "depth": depths, "is_seed": [True] + [False] * 25}
    )
    transfers = [
        (gids[0], gids[1], "2026-07-01", 10_000.0),
        (gids[0], gids[1], "2026-07-02", 10_000.0),
        (gids[1], gids[2], "2026-07-03", 10_000.0),
        (gids[2], gids[3], "2026-07-04", 5_000.0),
        (gids[3], gids[4], "2026-07-05", 5_000.0),
        (gids[1], gids[5], "2026-07-06", 7_500.0),
    ]
    transactions = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    transactions["date"] = pd.to_datetime(transactions["date"])
    edges = transactions.groupby(["src", "dst"], as_index=False).agg(
        sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size")
    )
    depth_by_gid = dict(zip(gids, depths))
    edges["depth"] = edges["dst"].map(depth_by_gid)
    return edges, nodes, transactions


@pytest.fixture
def data_dir(tmp_path, valid_frames):
    edges, nodes, transactions = valid_frames
    path = tmp_path / "data"
    path.mkdir()
    edges.to_parquet(path / "edges.parquet", index=False)
    nodes.to_parquet(path / "nodes.parquet", index=False)
    transactions.to_parquet(path / "transactions.parquet", index=False)
    return path
