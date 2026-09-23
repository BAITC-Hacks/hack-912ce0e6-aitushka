import pandas as pd
import pytest

from money_graph.temporal import temporal_features


def analyze(transfers, window=2):
    nodes = pd.DataFrame({"gid": [1, 2, 3]})
    transactions = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    return temporal_features(nodes, transactions, window).set_index("gid").loc[2]


def test_one_incoming_amount_cannot_match_multiple_outgoing_amounts_twice():
    row = analyze([
        (1, 2, "2026-07-01", 100.0),
        (2, 3, "2026-07-02", 80.0),
        (2, 3, "2026-07-03", 80.0),
    ])
    assert row["temporal_matched_kzt"] == pytest.approx(100.0)
    assert row["temporal_share"] == pytest.approx(1.0)
    assert row["temporal_matched_days"] == 2


def test_one_outgoing_amount_cannot_be_matched_against_each_incoming_amount():
    row = analyze([
        (1, 2, "2026-07-01", 60.0),
        (1, 2, "2026-07-02", 60.0),
        (2, 3, "2026-07-03", 100.0),
    ])
    assert row["temporal_matched_kzt"] == pytest.approx(100.0)


def test_same_day_flow_does_not_establish_order():
    row = analyze([
        (1, 2, "2026-07-01", 100.0),
        (2, 3, "2026-07-01", 100.0),
    ])
    assert row["temporal_matched_kzt"] == 0
    assert row["temporal_share"] == 0
    assert row["same_day_flow_days"] == 1


@pytest.mark.parametrize("outgoing_day", ["2026-06-30", "2026-07-04"])
def test_outgoing_before_incoming_or_after_window_does_not_match(outgoing_day):
    row = analyze([
        (1, 2, "2026-07-01", 100.0),
        (2, 3, outgoing_day, 100.0),
    ])
    assert row["temporal_matched_kzt"] == 0


def test_fifo_consumes_oldest_eligible_inflow_before_it_expires():
    row = analyze([
        (1, 2, "2026-07-01", 100.0),
        (1, 2, "2026-07-02", 100.0),
        (2, 3, "2026-07-02", 100.0),
        (2, 3, "2026-07-04", 100.0),
    ])
    assert row["temporal_matched_kzt"] == pytest.approx(200.0)


def test_self_transfer_is_not_evidence_of_transit():
    row = analyze([
        (2, 2, "2026-07-01", 100.0),
        (2, 2, "2026-07-02", 100.0),
    ])
    assert row["temporal_matched_kzt"] == 0
    assert row["temporal_share"] == 0
