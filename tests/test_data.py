import numpy as np
import pandas as pd
import pytest

from money_graph.data import DataValidationError, load_data, validate_data


def test_valid_frames_are_accepted(valid_frames, config):
    assert isinstance(validate_data(*valid_frames, config), dict)


@pytest.mark.parametrize("frame_index,column", [(0, "sum_kzt"), (1, "gid"), (2, "date")])
def test_missing_required_columns_fail_explicitly(valid_frames, config, frame_index, column):
    frames = [frame.copy() for frame in valid_frames]
    frames[frame_index] = frames[frame_index].drop(columns=[column])
    with pytest.raises(DataValidationError):
        validate_data(*frames, config)


@pytest.mark.parametrize("field", ["sum_kzt", "n_tx"])
def test_aggregate_amount_and_count_must_match_transactions(valid_frames, config, field):
    edges, nodes, transactions = [frame.copy() for frame in valid_frames]
    edges.loc[0, field] += 100 if field == "sum_kzt" else 1
    with pytest.raises(DataValidationError):
        validate_data(edges, nodes, transactions, config)


def test_equal_global_total_does_not_hide_per_pair_amount_mismatch(valid_frames, config):
    edges, nodes, transactions = [frame.copy() for frame in valid_frames]
    edges.loc[0, "sum_kzt"] += 100
    edges.loc[1, "sum_kzt"] -= 100
    with pytest.raises(DataValidationError):
        validate_data(edges, nodes, transactions, config)


def test_transaction_pair_must_have_an_aggregate_edge(valid_frames, config):
    edges, nodes, transactions = valid_frames
    with pytest.raises(DataValidationError):
        validate_data(edges.iloc[1:].copy(), nodes, transactions, config)


@pytest.mark.parametrize("endpoint", ["src", "dst"])
def test_unknown_endpoint_is_rejected_even_when_aggregates_match(valid_frames, config, endpoint):
    edges, nodes, transactions = [frame.copy() for frame in valid_frames]
    original = int(edges[endpoint].iloc[0])
    missing_gid = 2**53 + 1_000
    edges.loc[edges[endpoint] == original, endpoint] = missing_gid
    transactions.loc[transactions[endpoint] == original, endpoint] = missing_gid
    with pytest.raises(DataValidationError):
        validate_data(edges, nodes, transactions, config)


def test_duplicate_client_identity_is_rejected(valid_frames, config):
    edges, nodes, transactions = valid_frames
    duplicate_nodes = pd.concat([nodes, nodes.iloc[[0]]], ignore_index=True)
    with pytest.raises(DataValidationError):
        validate_data(edges, duplicate_nodes, transactions, config)


@pytest.mark.parametrize("amount", [-5_000.0, np.nan, np.inf])
def test_invalid_amounts_are_rejected(valid_frames, config, amount):
    edges, nodes, transactions = [frame.copy() for frame in valid_frames]
    transactions.loc[0, "sum_kzt"] = amount
    with pytest.raises(DataValidationError):
        validate_data(edges, nodes, transactions, config)


def test_loading_parquet_preserves_adjacent_large_int64_ids(data_dir, valid_frames):
    edges, nodes, transactions = load_data(data_dir)
    expected_edges, expected_nodes, expected_transactions = valid_frames
    assert nodes["gid"].tolist() == expected_nodes["gid"].tolist()
    assert edges[["src", "dst"]].equals(expected_edges[["src", "dst"]])
    assert transactions[["src", "dst"]].equals(expected_transactions[["src", "dst"]])
    assert nodes["gid"].nunique() == 26
