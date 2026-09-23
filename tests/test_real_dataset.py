"""Regression checks for the supplied hackathon dataset, when it is present."""

from pathlib import Path

import pytest
from pandas.testing import assert_frame_equal

from money_graph.pipeline import run_analysis


DATA = Path(__file__).resolve().parents[1] / "data"
HAS_DATA = all((DATA / name).is_file() for name in ["nodes.parquet", "edges.parquet", "transactions.parquet"])


@pytest.mark.skipif(not HAS_DATA, reason="The original hackathon Parquet files are not installed")
def test_original_dataset_sampled_centrality_and_results_are_reproducible(config):
    first = run_analysis(DATA, config)
    second = run_analysis(DATA, config)
    assert len(first.nodes) == 2248
    assert len(first.edges) == 3119
    assert len(first.transactions) == 4840
    assert len(first.top_nodes) >= 20
    boundary = first.nodes["depth"].eq(4)
    assert boundary.sum() == 444
    assert not first.nodes.loc[boundary, "role"].isin(["terminal", "transit"]).any()
    assert not first.nodes.loc[first.nodes["is_seed"], "role"].isin(["terminal", "transit"]).any()
    assert first.metadata["betweenness"]["sample_size"] < len(first.nodes)
    assert first.metadata["input_fingerprint"] == second.metadata["input_fingerprint"]
    assert_frame_equal(first.nodes, second.nodes, check_exact=True)
    assert_frame_equal(first.clusters, second.clusters, check_exact=True)
    assert_frame_equal(first.top_nodes, second.top_nodes, check_exact=True)
