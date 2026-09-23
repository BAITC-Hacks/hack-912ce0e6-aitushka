"""Explainable daily events; none of these observations establishes wrongdoing.

There is no ordering within one day. Bursts use other *active* days, while FIFO
compatibility only consumes receipts from previous calendar days. All evidence
references retain transaction occurrences, including equal rows in the export.
"""

from collections import deque
from datetime import date
from math import fsum
from statistics import median

from .event_data import EventIndex, make_pattern


DEFAULTS = {
    "burst_min_other_days": 5,
    "burst_min_count": 5,
    "burst_amount_min_count": 3,
    "burst_multiplier": 3.0,
    "group_min_payers": 3,
    "group_min_repeats": 2,
    "max_group_cores": 1000,
}

SAMPLE_LIMIT = (
    "Наблюдается только часть внутрибанковских переводов за выбранный период; "
    "отсутствие события не означает отсутствие другой активности."
)
SAME_DAY_LIMIT = "Известны даты без времени внутри дня: переводы в один день не обязательно одновременны."


def _participants(index, refs, gid):
    gids = {str(gid)}
    edges = set()
    for ref in refs:
        row = index.rows[ref]
        gids.update((row["src"], row["dst"]))
        edges.add((row["src"], row["dst"]))
    return sorted(gids, key=int), [
        {"src": src, "dst": dst}
        for src, dst in sorted(edges, key=lambda edge: (int(edge[0]), int(edge[1])))
    ]


def _burst_events(index, gid, direction, days, params):
    if len(days) <= params["burst_min_other_days"]:
        return []
    events = []
    label = "входящих" if direction == "in" else "исходящих"
    for day in days:
        others = [other for other in days if other["date"] != day["date"]]
        if len(others) < params["burst_min_other_days"]:
            continue
        count_baseline = float(median(other["n_tx"] for other in others))
        amount_baseline = float(median(other["sum_kzt"] for other in others))
        count_ratio = day["n_tx"] / count_baseline if count_baseline > 0 else None
        amount_ratio = day["sum_kzt"] / amount_baseline if amount_baseline > 0 else None
        count_hit = (
            day["n_tx"] >= params["burst_min_count"]
            and count_ratio is not None and count_ratio >= params["burst_multiplier"]
        )
        amount_hit = (
            day["n_tx"] >= params["burst_amount_min_count"]
            and amount_ratio is not None and amount_ratio >= params["burst_multiplier"]
        )
        if not (count_hit or amount_hit):
            continue
        metrics = []
        statements = []
        if count_hit:
            metrics.append("count")
            statements.append(f"{day['n_tx']} операций — в {count_ratio:.1f} раза больше медианы")
        if amount_hit:
            metrics.append("amount")
            statements.append(f"сумма в {amount_ratio:.1f} раза больше медианы")
        refs = day["evidence_refs"]
        gids, edges = _participants(index, refs, gid)
        events.append(make_pattern(
            kind="burst", focus_gid=gid, gids=gids,
            title=f"Всплеск {label} переводов",
            summary=f"{day['date']}: {'; '.join(statements)} других активных дней.",
            dates=[day["date"]],
            measurements={
                "direction": direction, "triggered_metrics": metrics,
                "n_tx": int(day["n_tx"]), "sum_kzt": float(day["sum_kzt"]),
                "counterparty_count": len(day["counterparties"]),
                "count_baseline": count_baseline, "amount_baseline": amount_baseline,
                "count_ratio": count_ratio, "amount_ratio": amount_ratio,
                "baseline_days": len(others),
                "baseline": [{"date": other["date"], "n_tx": int(other["n_tx"]),
                              "sum_kzt": float(other["sum_kzt"]),
                              "evidence_refs": list(other["evidence_refs"])} for other in others],
            },
            rule={
                "baseline": "median_of_other_active_days", "direction": direction,
                "min_other_days": params["burst_min_other_days"],
                "min_count": params["burst_min_count"],
                "amount_min_count": params["burst_amount_min_count"],
                "multiplier": params["burst_multiplier"],
            }, evidence_refs=refs, edges=edges,
            limitations=[
                "Порог — предварительная эвристика. Всплеск может соответствовать обычным расчётам или зарплате.",
                "Сравнение ретроспективное: используются остальные активные дни наблюдаемого периода.",
                SAMPLE_LIMIT,
            ],
        ))
    return events


def _group_events(index, gid, days, params):
    minimum = params["group_min_payers"]
    candidates = [day for day in days if len(day["counterparties"]) >= minimum]
    events = []
    for day in candidates:
        refs = day["evidence_refs"]
        gids, edges = _participants(index, refs, gid)
        events.append(make_pattern(
            kind="group_receipts", focus_gid=gid, gids=gids,
            title="Групповое поступление",
            summary=f"{day['date']}: поступления от {len(day['counterparties'])} разных плательщиков.",
            dates=[day["date"]],
            measurements={"payer_count": len(day["counterparties"]),
                          "payer_gids": list(day["counterparties"]),
                          "sum_kzt": float(day["sum_kzt"]), "n_tx": int(day["n_tx"])},
            rule={"min_payers": minimum, "period": "calendar_day"},
            evidence_refs=refs, edges=edges,
            limitations=[SAME_DAY_LIMIT, "Общие поступления сами по себе не подтверждают согласованность плательщиков.", SAMPLE_LIMIT],
        ))
    if len(candidates) < params["group_min_repeats"]:
        return events, True

    # Closed intersections recover a recurring core even when additional payers
    # differ on each date. Each exact core is merged across all supporting days.
    cores = set()
    search_complete = True
    payer_sets = [frozenset(day["counterparties"]) for day in candidates]
    for payers in payer_sets:
        intersections = {core & payers for core in cores}
        additions = {core for core in intersections if len(core) >= minimum} | {payers}
        # Intersection families can grow exponentially. A deterministic cap keeps
        # runtime bounded; accepted cores are still checked against *all* dates.
        additions.difference_update(cores)
        room = params["max_group_cores"] - len(cores)
        ordered = sorted(additions, key=lambda core: (len(core), tuple(sorted(map(int, core)))))
        cores.update(ordered[:room])
        if len(ordered) > room:
            search_complete = False
            break
    supported = {}
    for core in cores:
        support = tuple(i for i, payers in enumerate(payer_sets) if core <= payers)
        if len(support) >= params["group_min_repeats"]:
            # Same dates can have multiple subsets; retain their full common core.
            if support not in supported or len(core) > len(supported[support]):
                supported[support] = core
    groups = sorted(supported.items(), key=lambda item: (
        tuple(candidates[i]["date"] for i in item[0]), tuple(sorted(map(int, item[1])))
    ))
    for support, core in groups:
        refs = []
        occurrences = []
        for i in support:
            day = candidates[i]
            selected = sorted(ref for ref in day["evidence_refs"] if index.rows[ref]["src"] in core)
            refs.extend(selected)
            occurrences.append({"date": day["date"], "n_tx": len(selected),
                                "sum_kzt": fsum(index.rows[ref]["sum_kzt"] for ref in selected),
                                "evidence_refs": selected})
        gids, edges = _participants(index, refs, gid)
        events.append(make_pattern(
            kind="repeated_group", focus_gid=gid, gids=gids,
            title="Повторяющаяся группа плательщиков",
            summary=f"Одни и те же {len(core)} плательщика переводили клиенту в {len(support)} разные даты.",
            dates=[item["date"] for item in occurrences],
            measurements={"payer_gids": sorted(core, key=int), "payer_count": len(core),
                          "occurrence_count": len(support), "occurrences": occurrences,
                          "n_tx": len(refs), "sum_kzt": fsum(item["sum_kzt"] for item in occurrences)},
            rule={"min_payers": minimum, "min_repeats": params["group_min_repeats"],
                  "matching": "common_payer_core_on_distinct_dates"},
            evidence_refs=refs, edges=edges,
            limitations=[SAME_DAY_LIMIT,
                         "Повторение может быть регулярным законным расчётом; согласованность действий не установлена.",
                         SAMPLE_LIMIT] + ([] if search_complete else [
                             "Достигнут лимит перебора общих групп плательщиков; показана только найденная часть групп."
                         ]),
        ))
    return events, search_complete


def _transit_event(index, gid, incoming, outgoing, window_days):
    ins = {day["date"]: day for day in incoming}
    outs = {day["date"]: day for day in outgoing}
    pending = deque()
    matches = []
    for day in sorted(set(ins) | set(outs)):
        current = date.fromisoformat(day)
        while pending and (current - pending[0][0]).days > window_days:
            pending.popleft()
        available_out = float(outs.get(day, {}).get("sum_kzt", 0))
        while available_out > 0 and pending:
            used = min(available_out, pending[0][1])
            available_out -= used
            pending[0][1] -= used
            incoming_day = pending[0][0].isoformat()
            if used > 0:
                matches.append({"incoming_date": incoming_day, "outgoing_date": day,
                                "matched_kzt": used,
                                "incoming_evidence_refs": list(ins[incoming_day]["evidence_refs"]),
                                "outgoing_evidence_refs": list(outs[day]["evidence_refs"])})
            if pending[0][1] <= 1e-8:
                pending.popleft()
        # Insert only after today's outgoing has been consumed: date alone never
        # determines ordering between incoming and outgoing on that same date.
        if ins.get(day, {}).get("sum_kzt", 0) > 0:
            pending.append([current, float(ins[day]["sum_kzt"])])
    if not matches:
        return None
    refs = sorted({ref for match in matches
                   for name in ("incoming_evidence_refs", "outgoing_evidence_refs")
                   for ref in match[name]})
    matched = fsum(match["matched_kzt"] for match in matches)
    incoming_total = fsum(day["sum_kzt"] for day in incoming)
    outgoing_total = fsum(day["sum_kzt"] for day in outgoing)
    denominator = min(incoming_total, outgoing_total)
    matched_days = len({match["outgoing_date"] for match in matches})
    gids, edges = _participants(index, refs, gid)
    return make_pattern(
        kind="transit_window", focus_gid=gid, gids=gids,
        title="Вход и последующий выход",
        summary=f"Выходы в {matched_days} разные даты совместимы с поступлениями за предыдущие 1–{window_days} дня.",
        dates=[match[key] for match in matches for key in ("incoming_date", "outgoing_date")],
        measurements={"matched_kzt": matched,
                      "temporal_share": min(matched / denominator, 1.0) if denominator > 0 else 0.0,
                      "matched_days": matched_days, "window_days": window_days,
                      "incoming_total_kzt": incoming_total, "outgoing_total_kzt": outgoing_total,
                      "matches": matches},
        rule={"matching": "daily_fifo", "min_lag_days": 1, "max_lag_days": window_days,
              "same_day_order": "unknown"}, evidence_refs=refs, edges=edges,
        limitations=[
            "FIFO показывает совместимость дневных сумм, а не происхождение конкретных денег; остатки на счёте неизвестны.",
            "Ссылки относятся ко всему входному и выходному пулу дня; matched_kzt не приписывается отдельному переводу.",
            "В начале и конце периода часть возможных предшествующих или последующих переводов не наблюдается.",
            SAMPLE_LIMIT,
        ],
    )


def detect_temporal_patterns(index: EventIndex, gids: list[str], config: dict) -> tuple[list[dict], dict[str, dict]]:
    """Return dated evidence events and explicit history sufficiency per client."""
    params = {**DEFAULTS, **config.get("patterns", {})}
    window_days = config["temporal_window_days"]
    events, status = [], {}
    for gid in sorted({str(gid) for gid in gids}, key=int):
        incoming = sorted(index.daily.get((gid, "in"), []), key=lambda item: item["date"])
        outgoing = sorted(index.daily.get((gid, "out"), []), key=lambda item: item["date"])
        in_sufficient = len(incoming) > params["burst_min_other_days"]
        out_sufficient = len(outgoing) > params["burst_min_other_days"]
        notes = []
        if not in_sufficient:
            notes.append("Недостаточно активных дней для оценки всплесков входящих переводов.")
        if not out_sufficient:
            notes.append("Недостаточно активных дней для оценки всплесков исходящих переводов.")
        status[gid] = {
            "in_active_days": len(incoming), "out_active_days": len(outgoing),
            "in_burst_history_sufficient": in_sufficient,
            "out_burst_history_sufficient": out_sufficient,
            "burst_history_sufficient": in_sufficient or out_sufficient, "notes": notes,
        }
        events.extend(_burst_events(index, gid, "in", incoming, params))
        events.extend(_burst_events(index, gid, "out", outgoing, params))
        group_events, group_search_complete = _group_events(index, gid, incoming, params)
        events.extend(group_events)
        status[gid]["repeated_group_search_complete"] = group_search_complete
        if not group_search_complete:
            notes.append(
                f"Поиск повторяющихся групп ограничен {params['max_group_cores']} общими наборами плательщиков; "
                "показана только найденная часть групп."
            )
        transit = _transit_event(index, gid, incoming, outgoing, window_days)
        if transit is not None:
            events.append(transit)
    events.sort(key=lambda item: (item["date_from"], item["date_to"], item["kind"], int(item["focus_gid"]), item["pattern_id"]))
    return events, status
