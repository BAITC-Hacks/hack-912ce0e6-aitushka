import networkx as nx
import pandas as pd
import pytest

from money_graph.event_data import build_event_index
from money_graph.route_patterns import detect_route_patterns


def analyze(transfers, **settings):
    frame = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    index = build_event_index(frame)
    graph = nx.DiGraph()
    graph.add_edges_from((int(row.src), int(row.dst)) for row in frame.itertuples())
    events, coverage = detect_route_patterns(index, graph, {"temporal_window_days": 2, "patterns": settings})
    return events, coverage, index


def kinds(events, kind):
    return [event for event in events if event["kind"] == kind]


def test_repeated_route_uses_distinct_edge_days_and_reports_actual_evidence():
    events, coverage, index = analyze([
        (1, 2, "2026-07-01", 100), (2, 3, "2026-07-02", 80),
        (1, 2, "2026-07-02", 70), (2, 3, "2026-07-03", 60),
        (2, 3, "2026-07-20", 999),
    ])
    route = kinds(events, "repeated_route")[0]
    assert route["focus_gid"] == "2"
    assert route["measurements"]["repeat_count"] == 2
    assert route["measurements"]["unique_start_days"] == 2
    used = [(step["src"], step["dst"], step["date"]) for episode in route["episodes"] for step in episode["steps"]]
    assert len(used) == len(set(used)) == 4
    assert len(route["evidence_refs"]) == 4
    assert all(index.rows[ref]["date"] != "2026-07-20" for ref in route["evidence_refs"])
    assert coverage["search_complete"]


def test_one_day_with_duplicate_rows_cannot_be_counted_as_two_repetitions():
    events, _, index = analyze([
        (1, 2, "2026-07-01", 100), (1, 2, "2026-07-01", 100),
        (2, 3, "2026-07-02", 100), (2, 3, "2026-07-03", 100),
    ])
    assert len(index.rows) == 4
    assert index.edge_days[("1", "2", "2026-07-01")]["n_tx"] == 2
    assert kinds(events, "repeated_route") == []


def test_one_outgoing_day_cannot_support_multiple_incoming_days():
    events, _, _ = analyze([
        (1, 2, "2026-07-01", 100), (1, 2, "2026-07-02", 100),
        (2, 3, "2026-07-03", 200),
    ])
    assert not kinds(events, "repeated_route")


@pytest.mark.parametrize("lag,expected", [(0, 0), (1, 1), (2, 1), (3, 0), (-1, 0)])
def test_route_lag_boundaries_and_reversed_dates(lag, expected):
    events, _, _ = analyze([
        (1, 2, "2026-07-02", 100), (2, 3, f"2026-07-{2 + lag:02d}", 100),
        (1, 2, "2026-07-12", 100), (2, 3, f"2026-07-{12 + lag:02d}", 100),
    ])
    assert len(kinds(events, "repeated_route")) == expected


def test_overlapping_routes_remain_separate_and_are_not_additive():
    events, coverage, _ = analyze([
        (1, 2, "2026-07-01", 100), (1, 2, "2026-07-10", 100),
        (2, 3, "2026-07-02", 90), (2, 3, "2026-07-11", 90),
        (2, 4, "2026-07-02", 90), (2, 4, "2026-07-11", 90),
    ])
    first, second = kinds(events, "repeated_route")
    assert set(first["evidence_refs"]) & set(second["evidence_refs"])
    assert not coverage["overlapping_patterns_are_additive"]
    assert "flow_amount" not in first["measurements"]


def test_cycle_is_rotation_unique_but_temporal_origin_can_be_any_node():
    events, _, _ = analyze([
        (1, 2, "2026-07-03", 100), (2, 3, "2026-07-01", 100),
        (3, 1, "2026-07-02", 100),
    ])
    cycles = kinds(events, "cycle")
    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle["measurements"]["cycle"] == ["1", "2", "3"]
    assert cycle["measurements"]["temporal_status"] == "chronological"
    assert [step["src"] for step in cycle["episodes"][0]["steps"]] == ["2", "3", "1"]
    assert "repeat_count" not in cycle["measurements"]


def test_reverse_directed_cycles_are_distinct():
    events, _, _ = analyze([
        (1, 2, "2026-07-01", 100), (2, 3, "2026-07-02", 100), (3, 1, "2026-07-03", 100),
        (2, 1, "2026-07-01", 100), (3, 2, "2026-07-02", 100), (1, 3, "2026-07-03", 100),
    ])
    triangles = [event for event in kinds(events, "cycle") if event["measurements"]["cycle_length"] == 3]
    assert {tuple(event["measurements"]["cycle"]) for event in triangles} == {("1", "2", "3"), ("1", "3", "2")}


def test_reversed_dates_do_not_establish_cycle_episode():
    events, _, _ = analyze([
        (1, 2, "2026-07-03", 100), (2, 3, "2026-07-02", 100), (3, 1, "2026-07-01", 100),
    ])
    cycle = kinds(events, "cycle")[0]
    assert cycle["measurements"]["temporal_status"] == "structural_only"
    assert cycle["episodes"] == []
    assert cycle["measurements"]["evidence_scope"] == "full_observed_period"
    assert len(cycle["evidence_refs"]) == 3


def test_same_day_cycle_evidence_is_explicitly_ambiguous():
    events, _, _ = analyze([
        (1, 2, "2026-07-01", 100), (2, 3, "2026-07-01", 100), (3, 1, "2026-07-01", 100),
    ])
    cycle = kinds(events, "cycle")[0]
    assert cycle["measurements"]["temporal_status"] == "date_order_unknown"
    assert cycle["episodes"][0]["temporal_status"] == "date_order_unknown"
    assert len(cycle["episodes"][0]["steps"]) == 3
    assert not kinds(events, "repeated_route")


def test_cycle_evidence_covers_only_one_supported_episode():
    events, _, index = analyze([
        (1, 2, "2026-07-01", 100), (2, 1, "2026-07-02", 90),
        (1, 2, "2026-07-20", 1000), (2, 1, "2026-07-21", 900),
    ])
    cycle = kinds(events, "cycle")[0]
    assert len(cycle["episodes"]) == 1
    assert len(cycle["evidence_refs"]) == 2
    assert {index.rows[ref]["date"] for ref in cycle["evidence_refs"]} == {"2026-07-01", "2026-07-02"}
    assert cycle["date_to"] == "2026-07-02"


def test_temporal_budget_does_not_claim_absence_of_unexamined_cycle_episode():
    events, coverage, _ = analyze([
        (1, 2, "2026-07-01", 100), (2, 1, "2026-07-02", 100),
    ], max_temporal_states=1)
    cycle = kinds(events, "cycle")[0]
    assert not coverage["search_complete"]
    assert coverage["temporal_states_used"] == 1
    assert coverage["limits_hit"] == ["max_temporal_states"]
    assert cycle["measurements"]["temporal_status"] == "not_evaluated"
    assert not cycle["measurements"]["temporal_evaluation_complete"]


def test_partial_route_repetition_is_a_lower_bound():
    events, coverage, _ = analyze([
        (1, 2, "2026-07-01", 100), (2, 3, "2026-07-02", 100),
        (1, 2, "2026-07-10", 100), (2, 3, "2026-07-11", 100),
        (1, 2, "2026-07-20", 100), (2, 3, "2026-07-21", 100),
    ], max_temporal_states=2, max_route_candidates=1)
    route = kinds(events, "repeated_route")[0]
    assert route["measurements"]["repeat_count"] == 2
    assert route["measurements"]["repeat_count_is_lower_bound"]
    assert not coverage["search_complete"]
    assert coverage["limits_hit"] == ["max_temporal_states"]


def test_exact_candidate_and_state_limits_are_not_false_truncation():
    events, coverage, _ = analyze([
        (1, 2, "2026-07-01", 100), (2, 3, "2026-07-02", 100),
        (1, 2, "2026-07-10", 100), (2, 3, "2026-07-11", 100),
    ], max_route_candidates=1, max_temporal_states=2)
    assert len(kinds(events, "repeated_route")) == 1
    assert coverage["search_complete"]
    assert coverage["limits_hit"] == []


@pytest.mark.parametrize("setting,expected", [("max_cycle_candidates", 1), ("max_route_candidates", 0)])
def test_candidate_caps_are_explicit(setting, expected):
    events, coverage, _ = analyze([
        (1, 2, "2026-07-01", 100), (2, 1, "2026-07-02", 100),
        (3, 4, "2026-07-01", 100), (4, 3, "2026-07-02", 100),
        (5, 6, "2026-07-01", 100), (6, 7, "2026-07-02", 100),
        (8, 9, "2026-07-01", 100), (9, 10, "2026-07-02", 100),
    ], **{setting: 1})
    assert not coverage["search_complete"]
    assert setting in coverage["limits_hit"]
    if expected:
        assert len(kinds(events, "cycle")) == expected


def test_self_loops_and_isolated_clients_do_not_create_patterns():
    events, coverage, _ = analyze([(1, 1, "2026-07-01", 100), (1, 1, "2026-07-02", 100)])
    assert events == []
    assert coverage["search_complete"]


def test_cycle_dead_end_exploration_is_bounded_even_without_short_cycles():
    # All clients belong to one strongly connected component, but the shortest
    # cycle has five edges. Bounding only *found* cycles would not stop DFS.
    layers = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]
    transfers = [(src, dst, "2026-07-01", 100)
                 for i, group in enumerate(layers) for src in group for dst in layers[(i + 1) % len(layers)]]
    events, coverage, _ = analyze(transfers, max_cycle_search_steps=5)
    assert not kinds(events, "cycle")
    assert not coverage["search_complete"]
    assert not coverage["cycle_search_complete"]
    assert coverage["cycle_search_steps_used"] == 5
    assert coverage["limits_hit"] == ["max_cycle_search_steps"]
    reversed_events, reversed_coverage, _ = analyze(list(reversed(transfers)), max_cycle_search_steps=5)
    assert (events, coverage) == (reversed_events, reversed_coverage)
    _, full_coverage, _ = analyze(transfers)
    assert full_coverage["search_complete"]


def test_exact_cycle_exploration_budget_does_not_falsely_report_truncation():
    transfers = [(1, 2, "2026-07-01", 100), (2, 1, "2026-07-02", 100)]
    _, reference, _ = analyze(transfers)
    events, coverage, _ = analyze(transfers, max_cycle_search_steps=reference["cycle_search_steps_used"])
    assert len(kinds(events, "cycle")) == 1
    assert coverage["search_complete"]
    assert coverage["limits_hit"] == []


def test_large_identifiers_and_input_order_are_preserved_deterministically():
    a, b, c = 100000003684369100, 10000000331309100, 10000000437046100
    transfers = [
        (a, b, "2026-07-01", 100), (b, c, "2026-07-02", 90),
        (a, b, "2026-07-10", 100), (b, c, "2026-07-11", 90),
        (c, a, "2026-07-03", 80), (a, b, "2026-07-01", 100),
    ]
    first, coverage, _ = analyze(transfers)
    second, again, _ = analyze(list(reversed(transfers)))
    assert first == second
    assert coverage == again
    assert {gid for event in first for gid in event["gids"]} == {str(a), str(b), str(c)}
    assert all(isinstance(edge["src"], str) for event in first for edge in event["edges"])


def test_cycle_length_bound_is_respected():
    transfers = [(1, 2, "2026-07-01", 100), (2, 3, "2026-07-02", 100),
                 (3, 4, "2026-07-03", 100), (4, 1, "2026-07-04", 100)]
    events, _, _ = analyze(transfers, cycle_max_length=3)
    assert not kinds(events, "cycle")
    events, _, _ = analyze(transfers, cycle_max_length=4)
    assert len(kinds(events, "cycle")) == 1


@pytest.mark.parametrize("settings", [{"cycle_max_length": 5}, {"route_min_repeats": 1}, {"max_temporal_states": 0}, {"max_cycle_search_steps": 0}])
def test_invalid_detection_config_fails(settings):
    with pytest.raises(ValueError):
        analyze([(1, 2, "2026-07-01", 100)], **settings)
