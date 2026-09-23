from collections import defaultdict
import json

import pandas as pd
import pytest

from money_graph.event_data import build_event_index
from money_graph.temporal import temporal_features
from money_graph.temporal_patterns import detect_temporal_patterns


def analyze(transfers, *, gids=None, window=2, patterns=None):
    frame = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    index = build_event_index(frame)
    if gids is None:
        gids = sorted({str(gid) for row in transfers for gid in row[:2]}, key=int)
    events, status = detect_temporal_patterns(index, gids, {"temporal_window_days": window, "patterns": patterns or {}})
    return events, status, index


def of_kind(events, kind, gid="2"):
    return [event for event in events if event["kind"] == kind and event["focus_gid"] == gid]


def test_sparse_month_reports_insufficient_history_instead_of_zero_baseline_burst():
    events, status, _ = analyze([(1, 2, "2026-07-01", 100)] * 8, gids=["2", "99"])
    assert not of_kind(events, "burst")
    assert not status["2"]["burst_history_sufficient"]
    assert status["2"]["in_active_days"] == 1
    assert status["99"]["in_active_days"] == 0
    assert status["99"]["notes"]


def test_burst_uses_other_active_days_and_keeps_equal_transaction_occurrences():
    transfers = [(1, 2, f"2026-07-{day:02d}", 10) for day in range(1, 6)]
    transfers += [(1, 2, "2026-07-31", 10)] * 5
    events, status, index = analyze(transfers)
    event, = of_kind(events, "burst")
    assert event["date_from"] == "2026-07-31"
    assert event["measurements"]["count_baseline"] == 1
    assert event["measurements"]["count_ratio"] == 5
    assert event["measurements"]["baseline_days"] == 5
    assert event["measurements"]["triggered_metrics"] == ["count", "amount"]
    assert len(event["evidence_refs"]) == 5
    assert len({index.rows[ref]["ref"] for ref in event["evidence_refs"]}) == 5
    assert status["2"]["in_burst_history_sufficient"]
    assert not status["2"]["out_burst_history_sufficient"]
    assert of_kind(events, "burst", "1")[0]["measurements"]["direction"] == "out"


def test_five_total_active_days_are_not_five_other_days():
    transfers = [(1, 2, f"2026-07-{day:02d}", 10) for day in range(1, 5)]
    transfers += [(1, 2, "2026-07-31", 10)] * 8
    events, status, _ = analyze(transfers)
    assert not of_kind(events, "burst")
    assert not status["2"]["in_burst_history_sufficient"]


def test_regular_activity_and_single_large_payment_are_not_bursts():
    regular = [(1, 2, f"2026-07-{day:02d}", 10) for day in range(1, 7) for _ in range(5)]
    assert not of_kind(analyze(regular)[0], "burst")
    single_large = [(1, 2, f"2026-07-{day:02d}", 10) for day in range(1, 6)]
    single_large.append((1, 2, "2026-07-31", 100000))
    assert not of_kind(analyze(single_large)[0], "burst")


def test_amount_burst_can_trigger_without_count_burst():
    transfers = [(1, 2, f"2026-07-{day:02d}", 10) for day in range(1, 6) for _ in range(3)]
    transfers += [(1, 2, "2026-07-31", 30)] * 3
    event, = of_kind(analyze(transfers)[0], "burst")
    assert event["measurements"]["triggered_metrics"] == ["amount"]
    assert event["measurements"]["amount_ratio"] == 3


def test_group_counts_distinct_payers_and_excludes_self_transfers():
    transfers = [(1, 2, "2026-07-01", 10)] * 10
    transfers += [(2, 2, "2026-07-01", 500), (3, 2, "2026-07-01", 10)]
    assert not of_kind(analyze(transfers)[0], "group_receipts")
    transfers.append((4, 2, "2026-07-01", 10))
    events, _, index = analyze(transfers)
    event, = of_kind(events, "group_receipts")
    assert event["measurements"]["payer_count"] == 3
    assert event["measurements"]["n_tx"] == 12
    assert len(event["evidence_refs"]) == 12
    assert all(index.rows[ref]["src"] != index.rows[ref]["dst"] for ref in event["evidence_refs"])


def test_repeated_group_merges_dates_and_support_contains_only_common_payers():
    transfers = [(payer, 2, f"2026-07-{day:02d}", 10)
                 for day, extra in [(1, 6), (2, 7), (3, 8)] for payer in [1, 3, 4, extra]]
    events, _, index = analyze(transfers)
    event, = of_kind(events, "repeated_group")
    assert event["measurements"]["payer_gids"] == ["1", "3", "4"]
    assert event["measurements"]["occurrence_count"] == 3
    assert event["measurements"]["sum_kzt"] == 90
    assert len(event["evidence_refs"]) == 9
    assert {index.rows[ref]["src"] for ref in event["evidence_refs"]} == {"1", "3", "4"}
    assert event["gids"] == ["1", "2", "3", "4"]
    assert event["edges"] == [{"src": gid, "dst": "2"} for gid in ["1", "3", "4"]]


def test_three_date_common_core_is_found_even_when_pairwise_cores_are_larger():
    transfers = [(payer, 2, f"2026-07-{day:02d}", 10)
                 for day, payers in [(1, [1, 3, 4, 5, 6]), (2, [1, 3, 4, 6, 7]), (3, [1, 3, 4, 5, 7])]
                 for payer in payers]
    events, _, _ = analyze(transfers, patterns={"group_min_repeats": 3})
    event, = of_kind(events, "repeated_group")
    assert event["measurements"]["payer_gids"] == ["1", "3", "4"]
    assert event["measurements"]["occurrence_count"] == 3


def test_different_groups_do_not_become_repeated_group():
    transfers = [(payer, 2, "2026-07-01", 10) for payer in [1, 3, 4]]
    transfers += [(payer, 2, "2026-07-02", 10) for payer in [5, 6, 7]]
    assert not of_kind(analyze(transfers)[0], "repeated_group")


def test_repeated_group_cap_is_reported_and_deterministic():
    transfers = [(payer, 2, f"2026-07-{day:02d}", 10)
                 for day, payers in [(1, [1, 3, 4, 5]), (2, [1, 3, 4, 6]), (3, [1, 3, 4, 5])]
                 for payer in payers]
    events, status, index = analyze(transfers, patterns={"max_group_cores": 1})
    assert not status["2"]["repeated_group_search_complete"]
    assert any("ограничен" in note for note in status["2"]["notes"])
    event, = of_kind(events, "repeated_group")
    assert event["measurements"]["occurrence_count"] == 2
    assert any("лимит" in note for note in event["limitations"])
    assert all(index.rows[ref]["src"] in event["measurements"]["payer_gids"] for ref in event["evidence_refs"])
    assert analyze(list(reversed(transfers)), patterns={"max_group_cores": 1})[:2] == (events, status)


@pytest.mark.parametrize("outgoing_day", ["2026-07-01", "2026-06-30", "2026-07-04"])
def test_same_day_backward_and_expired_flow_do_not_establish_transit(outgoing_day):
    events, _, _ = analyze([(1, 2, "2026-07-01", 100), (2, 3, outgoing_day, 100)])
    assert not of_kind(events, "transit_window")


def test_fifo_journal_agrees_with_existing_features_without_double_counting():
    transfers = [(1, 2, "2026-07-01", 60), (4, 2, "2026-07-01", 40),
                 (2, 3, "2026-07-02", 80), (1, 2, "2026-07-02", 50),
                 (2, 3, "2026-07-03", 80), (2, 3, "2026-07-04", 100)]
    events, _, index = analyze(transfers)
    event, = of_kind(events, "transit_window")
    measured = event["measurements"]
    frame = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    previous = temporal_features(pd.DataFrame({"gid": [2]}), frame).iloc[0]
    assert measured["matched_kzt"] == pytest.approx(previous.temporal_matched_kzt)
    assert measured["temporal_share"] == pytest.approx(previous.temporal_share)
    assert measured["matched_days"] == previous.temporal_matched_days
    in_allocated, out_allocated = defaultdict(float), defaultdict(float)
    for match in measured["matches"]:
        in_allocated[match["incoming_date"]] += match["matched_kzt"]
        out_allocated[match["outgoing_date"]] += match["matched_kzt"]
        assert 1 <= (pd.Timestamp(match["outgoing_date"]) - pd.Timestamp(match["incoming_date"])).days <= 2
    for direction, totals in [("in", in_allocated), ("out", out_allocated)]:
        amounts = {day["date"]: day["sum_kzt"] for day in index.daily[("2", direction)]}
        assert all(amount <= amounts[day] for day, amount in totals.items())
    assert measured["matched_kzt"] == 150


def test_period_boundaries_do_not_wrap_or_invent_unobserved_money():
    transfers = [(2, 3, "2026-07-01", 100), (1, 2, "2026-07-31", 100)]
    assert not of_kind(analyze(transfers)[0], "transit_window")


def test_reordering_input_preserves_patterns_status_and_duplicate_refs():
    transfers = [(1, 2, f"2026-07-{day:02d}", 10) for day in range(1, 7)]
    transfers += [(payer, 2, day, 10) for payer in [1, 3, 4] for day in ["2026-07-07", "2026-07-08"]]
    transfers += [(2, 5, "2026-07-09", 10)] * 5
    first = analyze(transfers)
    second = analyze(list(reversed(transfers)))
    assert first[:2] == second[:2]
    assert first[2].rows == second[2].rows
    json.dumps(first[:2], allow_nan=False)
