import pandas as pd
import pytest

from money_graph.ranking import rank_nodes
from money_graph.roles import assign_roles


def feature_row(**overrides):
    row = {
        "gid": 101,
        "depth": 2,
        "is_seed": False,
        "in_deg": 1,
        "out_deg": 1,
        "in_kzt": 100_000.0,
        "out_kzt": 100_000.0,
        "in_tx": 1,
        "out_tx": 1,
        "pass_through": 1.0,
        "betweenness": 0.0,
        "pagerank": 0.01,
        "seed_reach": 1,
        "intercluster_degree": 0,
        "truncated_by_depth": False,
        "cluster_id": 0,
        "temporal_share": 0.0,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("special", [
    {"depth": 4, "truncated_by_depth": True},
    {"depth": 0, "is_seed": True},
])
@pytest.mark.parametrize("out_deg,out_kzt,ratio", [(0, 0.0, 0.0), (1, 100_000.0, 1.0)])
def test_censored_nodes_and_seeds_are_not_declared_terminal_or_transit(
    config, special, out_deg, out_kzt, ratio
):
    features = pd.DataFrame([
        feature_row(out_deg=out_deg, out_kzt=out_kzt, pass_through=ratio, **special)
    ])
    result = assign_roles(features, config)
    assert result.iloc[0]["role"] not in {"terminal", "transit"}


def test_observed_nonseed_sink_can_be_a_terminal_candidate(config):
    features = pd.DataFrame([feature_row(out_deg=0, out_kzt=0, pass_through=0)])
    result = assign_roles(features, config)
    assert result.iloc[0]["role"] == "terminal"


def test_observed_balanced_flows_can_be_a_transit_candidate(config):
    result = assign_roles(pd.DataFrame([feature_row()]), config)
    assert result.iloc[0]["role"] == "transit"


def test_distributor_requires_fan_out_relative_to_fan_in(config):
    features = pd.DataFrame([
        feature_row(in_deg=100, out_deg=10, pass_through=0.2, out_kzt=20_000),
        feature_row(gid=102, in_deg=1, out_deg=10, out_kzt=300_000, pass_through=3.0),
    ])
    roles = assign_roles(features, config).set_index("gid")["role"]
    assert roles.loc[101] != "distributor"
    assert roles.loc[102] == "distributor"


def test_isolate_has_a_role_and_no_spurious_terminal_assignment(config):
    features = pd.DataFrame([
        feature_row(in_deg=0, out_deg=0, in_kzt=0, out_kzt=0, pass_through=float("nan"), seed_reach=0)
    ])
    result = assign_roles(features, config)
    assert result.iloc[0]["role"] == "peripheral"
    assert 0 <= result.iloc[0]["role_score"] <= 1


def test_priority_does_not_penalize_boundary_or_depend_on_role_strength(config):
    features = pd.DataFrame([
        feature_row(gid=101, in_deg=10, seed_reach=3, betweenness=0.2),
        feature_row(gid=102, in_deg=10, seed_reach=3, betweenness=0.2,
                    depth=4, truncated_by_depth=True),
    ])
    classified = assign_roles(features, config)
    classified.loc[classified["gid"] == 101, "role_score"] = 0.01
    classified.loc[classified["gid"] == 102, "role_score"] = 0.99
    ranked, _, _ = rank_nodes(classified, config)
    scores = ranked.set_index("gid")["priority_score"]
    assert scores.loc[101] == pytest.approx(scores.loc[102])


def test_zero_features_produce_zero_priority_and_stable_gid_ties(config):
    features = pd.DataFrame([
        feature_row(gid=gid, in_deg=0, out_deg=0, in_kzt=0, out_kzt=0,
                    seed_reach=0, pass_through=float("nan"))
        for gid in [103, 101, 102]
    ])
    classified = assign_roles(features, config)
    ranked, top, _ = rank_nodes(classified, config)
    assert ranked["priority_score"].eq(0).all()
    assert top["gid"].tolist() == [101, 102, 103]
    assert top["rank"].tolist() == [1, 2, 3]
