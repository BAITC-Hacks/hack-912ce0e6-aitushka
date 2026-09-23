import json

import pandas as pd
from pandas.testing import assert_frame_equal

from money_graph.export import export_result, validate_outputs
from money_graph.pipeline import run_analysis


ALLOWED_ROLES = {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"}


def test_full_pipeline_preserves_every_client_and_output_contracts(data_dir, valid_frames, config, tmp_path):
    _, input_nodes, _ = valid_frames
    result = run_analysis(data_dir, config)
    validate_outputs(result)
    assert len(result.nodes) == len(input_nodes)
    assert set(result.nodes["gid"]) == set(input_nodes["gid"])
    assert not result.nodes["gid"].duplicated().any()
    assert set(result.nodes["role"]).issubset(ALLOWED_ROLES)
    for column in ["role_score", "priority_score"]:
        assert result.nodes[column].between(0, 1).all()
    assert result.nodes["evidence"].str.len().between(1, 200).all()
    assert result.nodes["cluster_id"].notna().all()
    assert set(result.clusters["cluster_id"]) == set(result.nodes["cluster_id"])
    assert result.clusters["n_nodes"].sum() == len(input_nodes)
    assert result.clusters["n_seed"].sum() == input_nodes["is_seed"].sum()
    assert len(result.top_nodes) >= 20
    assert not result.top_nodes["gid"].duplicated().any()
    assert result.top_nodes["priority_score"].is_monotonic_decreasing

    destination = tmp_path / "export"
    export_result(result, destination)
    required = {
        "nodes_roles.csv": {"gid", "role", "role_score", "cluster_id", "priority_score", "evidence"},
        "clusters.csv": {"cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"},
        "top_nodes.csv": {"rank", "gid", "role", "priority_score", "why"},
    }
    for filename, fields in required.items():
        table = pd.read_csv(destination / filename)
        assert fields.issubset(table.columns)
        assert not table[list(fields)].isna().any().any()
    exported_nodes = pd.read_csv(destination / "nodes_roles.csv", dtype={"gid": "int64"})
    assert set(exported_nodes["gid"]) == set(input_nodes["gid"])
    metadata = json.loads((destination / "run_metadata.json").read_text(encoding="utf-8"))
    assert isinstance(metadata, dict)


def test_same_data_and_config_reproduce_results(data_dir, config):
    first = run_analysis(data_dir, config)
    second = run_analysis(data_dir, config)
    assert_frame_equal(first.nodes, second.nodes, check_exact=True)
    assert_frame_equal(first.clusters, second.clusters, check_exact=True)
    assert_frame_equal(first.top_nodes, second.top_nodes, check_exact=True)


def test_depth_boundary_survives_full_pipeline(data_dir, valid_frames, config):
    _, input_nodes, _ = valid_frames
    boundary_gid = int(input_nodes.loc[input_nodes["depth"] == 4, "gid"].iloc[0])
    result = run_analysis(data_dir, config)
    boundary = result.nodes.set_index("gid").loc[boundary_gid]
    assert boundary["role"] not in {"terminal", "transit"}
    assert bool(boundary["truncated_by_depth"])
