"""Local, reproducible analyst brief generated exclusively from evidence."""

from html import escape

from .visualization import ROLE_LABELS


def client_events(result, gid: str) -> list[dict]:
    order = {"repeated_route": 0, "repeated_group": 1, "burst": 2,
             "transit_window": 3, "cycle": 4, "group_receipts": 5}
    return sorted((event for event in result.patterns if gid in event["gids"]),
                  key=lambda event: (order.get(event["kind"], 9), event["date_from"] or "", event["pattern_id"]))


def build_report(result, gid: str) -> dict:
    found = result.nodes.loc[result.nodes.gid.map(str).eq(gid)]
    if found.empty:
        raise KeyError(gid)
    client = found.to_dict(orient="records")[0]
    client["gid"] = gid
    events = client_events(result, gid)
    suggestions = []
    if client["truncated_by_depth"]:
        suggestions.append("Запросить продолжение исходящих переводов за пределами четвёртого колена.")
    if client["is_seed"] or (client["in_kzt"] <= 0 and client["out_kzt"] > 0):
        suggestions.append("Запросить полную входящую историю и начальный остаток.")
    elif client["out_kzt"] > client["in_kzt"]:
        suggestions.append("Запросить начальный остаток и операции за соседний период.")
    if any(event["measurements"].get("temporal_status") == "date_order_unknown" for event in events):
        suggestions.append("Запросить точное время операций в одном дне, чтобы проверить порядок шагов.")
    alternate = client.get("alternative_role")
    if alternate not in (None, "", "none") and abs(float(client["role_score"]) - float(client["alternative_role_score"])) < .05:
        suggestions.append("Сопоставить основную и альтернативную роли: их оценки близки.")
    period_start = result.transactions.date.min().date().isoformat() if len(result.transactions) else None
    period_end = result.transactions.date.max().date().isoformat() if len(result.transactions) else None
    if events and any(event["date_from"] == period_start or event["date_to"] == period_end for event in events):
        suggestions.append("Расширить период наблюдения на соседние дни.")
    if not suggestions:
        suggestions.append("Проверить исходные операции и экономическое основание переводов.")
    refs = sorted({ref for event in events for ref in event["evidence_refs"]} |
                  {ref for event in events for day in event["measurements"].get("baseline", [])
                   for ref in day["evidence_refs"]})
    evidence = [result.event_index.rows[ref] for ref in refs] if result.event_index else []

    def top_edges(frame):
        return [{"src": str(row.src), "dst": str(row.dst), "sum_kzt": float(row.sum_kzt),
                 "n_tx": int(row.n_tx)} for row in frame.itertuples(index=False)]
    incoming = result.edges.loc[result.edges.dst.map(str).eq(gid)].sort_values(["sum_kzt", "src"], ascending=[False, True]).head(5)
    outgoing = result.edges.loc[result.edges.src.map(str).eq(gid)].sort_values(["sum_kzt", "dst"], ascending=[False, True]).head(5)
    return {"title": f"Справка аналитика · клиент {gid}",
            "fingerprint": result.metadata["input_fingerprint"], "created_at": result.metadata["created_at"],
            "rules_version": result.metadata.get("patterns", {}).get("rules_version", 1),
            "period": {"from": period_start, "to": period_end}, "client": client,
            "incoming": top_edges(incoming), "outgoing": top_edges(outgoing), "patterns": events,
            "status": result.pattern_status.get(gid, {}), "coverage": result.pattern_coverage,
            "recommendations": suggestions, "limitations": result.metadata["limitations"],
            "evidence": evidence, "parameters": result.metadata["config"]["patterns"]}


def render_report_html(report: dict) -> str:
    """Render an offline, escaped brief; full data remains available as JSON."""
    text = lambda value: escape(str(value), quote=True)
    money = lambda value: f"{float(value):,.2f}".replace(",", " ") + " ₸"
    client = report["client"]
    event_blocks = []
    for event in report["patterns"][:8]:
        episodes = []
        for number, episode in enumerate(event["episodes"][:3], 1):
            steps = "".join(f'<tr><td>{text(step["date"])}</td><td>{text(step["src"])} → {text(step["dst"])}</td><td>{money(step["sum_kzt"])}</td></tr>' for step in episode["steps"])
            episodes.append(f'<h4>Эпизод {number}</h4><table><tr><th>Дата</th><th>Направление</th><th>Сумма шага</th></tr>{steps}</table>')
        if len(event["episodes"]) > 3:
            episodes.append(f'<p class="muted">Показаны 3 из {len(event["episodes"])} эпизодов.</p>')
        refs = set(event["evidence_refs"])
        source = [row for row in report["evidence"] if row["ref"] in refs]
        rows = "".join(f'<tr><td>{text(row["date"])}</td><td>{text(row["src"])} → {text(row["dst"])}</td><td>{money(row["sum_kzt"])}</td><td>{text(row["ref"])}</td></tr>' for row in source)
        event_blocks.append(f'<section><h3>{text(event["title"])}</h3><p>{text(event["summary"])}</p><p class="muted">{text(event["date_from"])} — {text(event["date_to"])} · основной клиент {text(event["focus_gid"])}</p>{"".join(episodes)}<details><summary>Исходные операции ({len(source)})</summary><table><tr><th>Дата</th><th>Перевод</th><th>Сумма</th><th>ID строки</th></tr>{rows}</table></details></section>')
    coverage = "" if report["coverage"].get("search_complete", True) else '<p class="warning">Поиск событий ограничен вычислительным бюджетом. Список событий неполный.</p>'
    recommendations = "".join(f"<li>{text(item)}</li>" for item in report["recommendations"])
    limitations = "".join(f"<li>{text(item)}</li>" for item in report["limitations"])
    alternate = client.get("alternative_role")
    alt = f'<p>Альтернатива: {text(ROLE_LABELS.get(alternate, alternate))}</p>' if alternate not in (None, "", "none") else ""
    note = (f'Показаны 8 из {len(report["patterns"])} событий.' if len(report["patterns"]) > 8
            else f'Событий с участием клиента: {len(report["patterns"])}.')
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{text(report["title"])}</title><style>body{{font:15px/1.55 system-ui;color:#283541;background:#f1f3ef;padding:24px}}main{{max-width:960px;margin:auto;background:white;padding:36px;border-radius:20px}}section{{padding:18px;background:#f2f5f9;border-radius:12px;margin:16px 0}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{padding:7px;border-bottom:1px solid #dde3e9;text-align:left;overflow-wrap:anywhere}}.muted{{color:#657581}}.warning{{background:#fff2d9;padding:12px}}button{{padding:11px 16px;background:#263746;color:white;border:0;border-radius:10px}}@media print{{body,main{{padding:0;background:white}}button,details{{display:none}}section{{break-inside:avoid}}}}</style></head><body><main><button onclick="window.print()">Печать / сохранить в PDF</button><h1>{text(report["title"])}</h1><p>Период: {text(report["period"]["from"])} — {text(report["period"]["to"])}</p><h2>Роль и приоритет</h2><p><b>{text(ROLE_LABELS.get(client["role"], client["role"]))}</b> · сила признаков {float(client["role_score"]):.3f} · приоритет {float(client["priority_score"]):.3f}</p>{alt}<p>{text(client["evidence"])}</p><p>{text(client["why"])}</p><h2>События и маршруты</h2><p>{text(note)}</p>{coverage}{"".join(event_blocks)}<h2>Что проверить дальше</h2><ol>{recommendations}</ol><h2>Ограничения</h2><ul>{limitations}</ul><p class="muted">Расчёт: {text(report["created_at"])} · версия правил {text(report["rules_version"])}</p><p class="muted">Версия набора и расчёта: {text(report["fingerprint"])}</p></main></body></html>'''
