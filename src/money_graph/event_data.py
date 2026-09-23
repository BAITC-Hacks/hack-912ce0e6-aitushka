"""Stable transaction evidence and daily aggregates for analytic events."""

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from math import fsum

import pandas as pd


@dataclass(eq=True)
class EventIndex:
    rows: dict
    edge_days: dict
    edge_series: dict
    daily: dict


def _sorted_gids(values):
    return sorted({str(value) for value in values}, key=int)


def build_event_index(transactions: pd.DataFrame) -> EventIndex:
    """Keep identical observations distinct while making their refs reorder-stable."""
    canonical = sorted(
        ((str(int(row.src)), str(int(row.dst)), pd.Timestamp(row.date).date().isoformat(), float(row.sum_kzt))
         for row in transactions.itertuples(index=False)),
        key=lambda item: (item[2], int(item[0]), int(item[1]), item[3]),
    )
    occurrences = defaultdict(int)
    rows = {}
    edge_buckets = defaultdict(list)
    daily_buckets = defaultdict(list)
    for src, dst, day, amount in canonical:
        identity = json.dumps([src, dst, day, amount], ensure_ascii=False, separators=(",", ":"))
        digest = sha256(identity.encode("utf-8")).hexdigest()[:20]
        occurrences[digest] += 1
        ref = f"tx_{digest}_{occurrences[digest]}"
        row = {"ref": ref, "src": src, "dst": dst, "date": day, "sum_kzt": amount}
        rows[ref] = row
        if src == dst:
            continue
        edge_buckets[(src, dst, day)].append(row)
        daily_buckets[(src, "out", day)].append(row)
        daily_buckets[(dst, "in", day)].append(row)

    edge_days = {}
    edge_series = defaultdict(list)
    for key in sorted(edge_buckets, key=lambda item: (int(item[0]), int(item[1]), item[2])):
        src, dst, day = key
        values = edge_buckets[key]
        aggregate = {"src": src, "dst": dst, "date": day,
                     "sum_kzt": fsum(item["sum_kzt"] for item in values), "n_tx": len(values),
                     "evidence_refs": sorted(item["ref"] for item in values)}
        edge_days[key] = aggregate
        edge_series[(src, dst)].append(aggregate)

    daily = defaultdict(list)
    for (gid, direction, day), values in sorted(
            daily_buckets.items(), key=lambda item: (int(item[0][0]), item[0][1], item[0][2])):
        counterparties = (item["src"] if direction == "in" else item["dst"] for item in values)
        daily[(gid, direction)].append({
            "date": day, "sum_kzt": fsum(item["sum_kzt"] for item in values), "n_tx": len(values),
            "counterparties": _sorted_gids(counterparties),
            "evidence_refs": sorted(item["ref"] for item in values),
        })
    return EventIndex(rows, edge_days, dict(edge_series), dict(daily))


def make_pattern(*, kind, focus_gid, gids, title, summary, dates, measurements, rule,
                 evidence_refs, edges, limitations, episodes=None):
    """Build a deterministic, JSON-safe event envelope."""
    dates = sorted({str(day) for day in dates if day})
    gids = _sorted_gids(gids)
    refs = sorted(set(evidence_refs))
    edges = sorted(({"src": str(edge["src"]), "dst": str(edge["dst"])} for edge in edges),
                   key=lambda edge: (int(edge["src"]), int(edge["dst"])))
    episodes = list(episodes or [])
    identity = {"kind": kind, "focus_gid": str(focus_gid), "gids": gids, "dates": dates,
                "measurements": measurements, "rule": rule, "evidence_refs": refs,
                "edges": edges, "episodes": episodes}
    pattern_id = "pt_" + sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                              separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return {"pattern_id": pattern_id, "kind": kind, "focus_gid": str(focus_gid), "gids": gids,
            "title": title, "summary": summary, "date_from": dates[0] if dates else None,
            "date_to": dates[-1] if dates else None, "measurements": measurements, "rule": rule,
            "evidence_refs": refs, "edges": edges, "episodes": episodes,
            "limitations": list(limitations)}
