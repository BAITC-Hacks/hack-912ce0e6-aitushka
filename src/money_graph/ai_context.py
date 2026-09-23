"""Bounded evidence packets for AI; arithmetic and provenance stay in Python."""

from collections import Counter
import json
import re

from .analyst_report import build_report
from .visualization import ROLE_LABELS


MAX_EVENTS = 12
MAX_CONTEXT_CHARS = 42000
METHOD = (
    "Роль — гипотеза о положении в наблюдаемой сети. Приоритет — порядок проверки, "
    "а не вероятность нарушения. Даты не устанавливают порядок внутри дня. FIFO "
    "показывает совместимость дневных сумм; начальный остаток неизвестен. "
    "Разные события могут использовать одни операции; суммы шагов маршрута нельзя "
    "складывать в прослеженный объём. Отсутствие события не доказывает безопасность."
)


def prepare_context(result, gid, pattern_id=None):
    report = build_report(result, gid)
    client = report["client"]
    events = report["patterns"]
    if pattern_id:
        selected = next((event for event in events if event["pattern_id"] == pattern_id), None)
        if selected is None:
            raise ValueError("Событие не найдено у выбранного клиента.")
        chosen = [selected]
    else:
        # Round-robin prevents numerous routes from hiding bursts or FIFO.
        buckets = {}
        for event in events:
            buckets.setdefault(event["kind"], []).append(event)
        chosen = []
        while len(chosen) < MAX_EVENTS and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(chosen) < MAX_EVENTS:
                    chosen.append(bucket.pop(0))

    sources = []

    def add(label, data, event=None):
        source = {"id": f"F{len(sources) + 1}", "label": label, "data": data}
        if event:
            source["pattern"] = {key: event[key] for key in ("pattern_id", "title", "summary")}
        sources.append(source)

    add("Период и ограничения выборки", {"period": report["period"], "limitations": report["limitations"],
                                        "client_observation": client["observation_note"], "method": METHOD})
    add("Роль и основания приоритета", {
        "gid": gid, "role": ROLE_LABELS.get(client["role"], client["role"]),
        "role_score": float(client["role_score"]), "priority_score": float(client["priority_score"]),
        "evidence": client["evidence"], "why": client["why"],
        "alternative_role": ROLE_LABELS.get(client.get("alternative_role"), "нет"),
        "contributions": {key: float(client[key]) for key in client if key.startswith("contribution_")},
        "depth": int(client["depth"]), "is_seed": bool(client["is_seed"]),
    })
    add("Потоки клиента за весь период", {key: float(client[key]) for key in
        ("in_kzt", "out_kzt", "in_tx", "out_tx", "in_deg", "out_deg", "seed_reach",
         "temporal_matched_kzt", "temporal_share")})
    add("Главные контрагенты", {"incoming_top5": report["incoming"], "outgoing_top5": report["outgoing"]})
    tx = result.transactions
    rows = tx.loc[tx.src.map(str).eq(gid) | tx.dst.map(str).eq(gid)]
    daily = []
    for day, group in rows.groupby("date", sort=True):
        incoming = group.loc[group.dst.map(str).eq(gid)]
        outgoing = group.loc[group.src.map(str).eq(gid)]
        daily.append({"date": day.date().isoformat(), "in_kzt": float(incoming.sum_kzt.sum()),
                      "out_kzt": float(outgoing.sum_kzt.sum()), "in_tx": len(incoming), "out_tx": len(outgoing)})
    add("Дневные потоки", {"days": daily[:62], "total_active_days": len(daily), "truncated": len(daily) > 62})
    add("Полнота поиска и история", {"coverage": report["coverage"], "status": report["status"],
                                     "total_events": len(events), "kind_counts": dict(Counter(e["kind"] for e in events))})
    add("Следующие запросы данных", {"recommendations": report["recommendations"]})
    shown = 0
    for event in chosen:
        measurements = {key: value for key, value in event["measurements"].items()
                        if key not in ("baseline", "matches", "occurrences")}
        # Explicitly disclose truncated support, never claim the packet is exhaustive.
        for key in ("baseline", "matches", "occurrences"):
            if key in event["measurements"]:
                values = event["measurements"][key]
                measurements[key] = [{k: v for k, v in row.items() if "refs" not in k} for row in values[:8]]
                measurements[f"{key}_total"] = len(values)
        data = {"kind": event["kind"], "focus_gid": event["focus_gid"], "summary": event["summary"],
                "date_from": event["date_from"], "date_to": event["date_to"], "measurements": measurements,
                "rule": event["rule"], "limitations": event["limitations"],
                "episodes": [{"steps": [{k: v for k, v in step.items() if k != "evidence_refs"}
                                         for step in episode["steps"]]} for episode in event["episodes"][:4]],
                "total_episodes": len(event["episodes"]), "source_operation_count": len(event["evidence_refs"])}
        if len(json.dumps(sources + [data], ensure_ascii=False)) > MAX_CONTEXT_CHARS:
            break
        add(event["title"], data, event)
        shown += 1
    scope = {"total_events": len(events), "included_events": shown, "selected_event": bool(pattern_id),
             "events_truncated": shown < (1 if pattern_id else len(events)), "daily_truncated": len(daily) > 62}
    # Only participants actually present in this packet may be restored in an answer.
    mentioned = {gid}
    def collect_ids(value):
        if isinstance(value, str):
            mentioned.update(re.findall(r'(?<![\w.])\d{16,20}(?![\w.])', value))
        elif isinstance(value, dict):
            for item in value.values():
                collect_ids(item)
        elif isinstance(value, list):
            for item in value:
                collect_ids(item)
    collect_ids(sources)
    gids = sorted((set(result.nodes.gid.map(str)) & mentioned) | {gid}, key=int)
    aliases = {value: f"Участник_{index + 1}" for index, value in enumerate(gids)}

    def mask(value):
        return re.sub(r'(?<![\w.])\d{16,20}(?![\w.])', lambda match: aliases.get(match.group(), "Неизвестный_участник"), value)

    def mask_data(value):
        if isinstance(value, str):
            return mask(value)
        if isinstance(value, dict):
            return {key: mask_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [mask_data(item) for item in value]
        return value

    # Only locally resolvable F identifiers reach the model; original IDs stay here.
    provider_sources = [{"id": source["id"], "label": source["label"], "data": source["data"]} for source in sources]
    provider_packet = {"selected_client": aliases[gid], "scope": scope,
                       "sources": mask_data(provider_sources)}
    return {"sources": sources, "scope": scope, "provider_packet": provider_packet,
            "aliases": aliases, "mask": mask}
