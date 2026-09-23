"""Conservative daily FIFO matching: compatibility, not tracing actual money."""

from collections import deque

import pandas as pd


def temporal_features(nodes: pd.DataFrame, transactions: pd.DataFrame, window_days: int = 2):
    # Self-transfers carry no evidence of transit to another client.
    tx = transactions.loc[transactions.src.ne(transactions.dst)].copy()
    tx["date"] = pd.to_datetime(tx.date).dt.normalize()
    outgoing = tx.groupby(["src", "date"]).sum_kzt.sum()
    incoming = tx.groupby(["dst", "date"]).sum_kzt.sum()
    rows = []
    for gid in nodes.gid:
        ins = incoming.loc[gid].to_dict() if gid in incoming.index.get_level_values(0) else {}
        outs = outgoing.loc[gid].to_dict() if gid in outgoing.index.get_level_values(0) else {}
        dates = sorted(set(ins) | set(outs))
        pending = deque()
        matched = 0.0
        matched_days = 0
        same_day = 0
        for day in dates:
            while pending and (day - pending[0][0]).days > window_days:
                pending.popleft()
            amount = float(outs.get(day, 0))
            today_matched = 0.0
            # Only earlier days are in the queue; same-day order is unknown.
            while amount > 0 and pending:
                used = min(amount, pending[0][1])
                amount -= used
                pending[0][1] -= used
                today_matched += used
                if pending[0][1] <= 1e-8:
                    pending.popleft()
            if ins.get(day, 0) > 0:
                pending.append([day, float(ins[day])])
            matched += today_matched
            matched_days += int(today_matched > 0)
            same_day += int(ins.get(day, 0) > 0 and outs.get(day, 0) > 0)
        denominator = min(sum(ins.values()), sum(outs.values()))
        rows.append((int(gid), matched, min(matched / denominator, 1.0) if denominator > 0 else 0.0, len(dates), matched_days, same_day))
    return pd.DataFrame(rows, columns=["gid", "temporal_matched_kzt", "temporal_share", "active_days", "temporal_matched_days", "same_day_flow_days"])

