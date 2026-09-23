"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icon";
import { ClientPatternsResult, GraphPatternSelection, Pattern, PatternDetail, PatternKind, formatNumber, request, shortDate } from "@/lib/types";

const patternLabels: Record<PatternKind, string> = {
  burst: "Всплески активности", group_receipts: "Групповые поступления", repeated_group: "Повтор состава",
  transit_window: "Последующий выход", repeated_route: "Повтор маршрута", cycle: "Циклы",
};
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Не удалось загрузить события. Повторите попытку.";
const pageSize = 10;

export function ClientPatterns({ gid, refresh, openClient, openPattern }: {
  gid: string; refresh: number; openClient: (gid: string) => void; openPattern: (selection: GraphPatternSelection) => void;
}) {
  const [kind, setKind] = useState<PatternKind | "all">("all");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<ClientPatternsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<PatternDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const downloadRequest = useRef<AbortController | null>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setResult(null); setSelectedId(""); setDetail(null); setDetailError("");
    request<ClientPatternsResult>(`/api/clients/${encodeURIComponent(gid)}/patterns?kind=${kind}&limit=${pageSize}&offset=${offset}`, controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult(data); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [gid, refresh, kind, offset, revision]);

  useEffect(() => {
    if (!selectedId || !result) return;
    const controller = new AbortController();
    setDetailLoading(true); setDetailError(""); setDetail(null);
    request<PatternDetail>(`/api/patterns/${encodeURIComponent(selectedId)}?fingerprint=${encodeURIComponent(result.fingerprint)}`, controller.signal)
      .then(data => { if (!controller.signal.aborted) setDetail(data); })
      .catch(cause => { if (!controller.signal.aborted) setDetailError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, result]);

  useEffect(() => { if (detail) detailHeading.current?.focus({ preventScroll: true }); }, [detail]);
  useEffect(() => () => downloadRequest.current?.abort(), []);

  async function downloadReport() {
    if (!result || downloading) return;
    const controller = new AbortController(); downloadRequest.current = controller;
    setDownloading(true); setDownloadError("");
    try {
      const response = await fetch(`/api/clients/${encodeURIComponent(gid)}/report?format=html&fingerprint=${encodeURIComponent(result.fingerprint)}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) {
        let message = "Не удалось скачать справку. Обновите события и повторите попытку.";
        try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch { /* Proxy errors may not contain JSON. */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = `analyst_report_${gid}.html`; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { if (!controller.signal.aborted) setDownloadError(errorMessage(cause)); }
    finally { if (!controller.signal.aborted) setDownloading(false); }
  }

  const status = result?.status;
  return <section className="card patterns-section" aria-labelledby="patterns-heading">
    <div className="section-heading patterns-heading"><div><h2 id="patterns-heading">События и маршруты</h2><p>Наблюдаемые совпадения правил и операции, на которых они основаны.</p></div>
      <button className="button secondary" onClick={downloadReport} disabled={!result || loading || downloading}><Icon name="download" size={17}/>{downloading ? "Готовим справку…" : "Скачать справку"}</button>
    </div>
    <div className="patterns-toolbar"><label htmlFor="pattern-kind">Тип события<select id="pattern-kind" value={kind} onChange={event => { setKind(event.target.value as PatternKind | "all"); setOffset(0); }}><option value="all">Все события</option>{Object.entries(patternLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><p>Справка содержит основания, ограничения и следующие шаги. HTML можно открыть и напечатать в PDF.</p></div>
    {downloadError && <div className="notice error compact" role="alert">{downloadError}</div>}
    {loading ? <div className="patterns-loading" role="status"><span className="spinner"/>Загружаем события…</div> : error ? <div className="notice error" role="alert"><p>{error}</p><button className="button secondary" onClick={() => setRevision(value => value + 1)}>Повторить</button></div> : result && <>
      <div className="patterns-context"><span>{formatNumber(result.total)} событий{kind !== "all" ? " выбранного типа" : " у клиента"}</span>{status && <span>Активных дней: вход {status.in_active_days}, выход {status.out_active_days}</span>}</div>
      {result.coverage.search_complete === false && <div className="patterns-note"><Icon name="info" size={17}/><p>Поиск ограничен вычислительным лимитом. Показаны найденные события; отсутствие других совпадений не означает, что они исключены.</p></div>}
      {(status?.notes || []).length > 0 && <div className="patterns-note"><Icon name="info" size={17}/><div>{status!.notes.map(note => <p key={note}>{note}</p>)}</div></div>}
      {!result.items.length ? <div className="patterns-empty"><h3>Совпадений {kind === "all" ? "не найдено" : "этого типа не найдено"}</h3><p>Это результат проверки заданных правил в наблюдаемой выборке. Недостаточная история и ограничения поиска учитываются отдельно.</p>{kind !== "all" && <button className="text-button" onClick={() => { setKind("all"); setOffset(0); }}>Показать все типы<Icon name="arrow" size={15}/></button>}</div> : <>
        <div className="pattern-list">{result.items.map(pattern => <div className="pattern-item" key={pattern.pattern_id}>
          <button className={`pattern-summary ${selectedId === pattern.pattern_id ? "selected" : ""}`} aria-expanded={selectedId === pattern.pattern_id} aria-controls={`detail-${pattern.pattern_id}`} onClick={() => setSelectedId(previous => previous === pattern.pattern_id ? "" : pattern.pattern_id)}>
            <span className="pattern-kind-icon"><Icon name={pattern.kind === "cycle" || pattern.kind === "repeated_route" ? "graph" : "clock"} size={20}/></span><span className="pattern-summary-body"><span className="pattern-meta">{patternLabels[pattern.kind]} · {shortDate(pattern.date_from)}{pattern.date_to !== pattern.date_from && ` — ${shortDate(pattern.date_to)}`}</span><strong>{pattern.title}</strong><span>{pattern.summary}</span></span><Icon name="chevron" size={18}/>
          </button>
          {selectedId === pattern.pattern_id && <div className="pattern-detail" id={`detail-${pattern.pattern_id}`}>
            {detailLoading ? <p role="status" className="patterns-loading"><span className="spinner"/>Загружаем исходные операции…</p> : detailError ? <div className="notice error" role="alert"><p>{detailError}</p><button className="button secondary" onClick={() => setRevision(value => value + 1)}>Обновить события</button></div> : detail?.pattern.pattern_id === pattern.pattern_id && <>
              <div className="pattern-detail-heading"><h3 ref={detailHeading} tabIndex={-1}>Основания события</h3><button className="button dark" onClick={() => openPattern({ pattern_id: detail.pattern.pattern_id, fingerprint: detail.fingerprint, title: detail.pattern.title, summary: detail.pattern.summary })}><Icon name="graph" size={16}/>Показать на графе</button></div>
              <PatternEvidence pattern={detail.pattern} selectedGid={gid} openClient={openClient}/>
              <TransactionEvidence title="Исходные операции" transactions={detail.transactions} openClient={openClient}/>
              {!!detail.comparison_transactions?.length && <TransactionEvidence title="Операции других активных дней для сравнения" transactions={detail.comparison_transactions} openClient={openClient}/>}
            </>}
          </div>}
        </div>)}</div>
        <div className="patterns-pagination"><span>{offset + 1}–{offset + result.items.length} из {formatNumber(result.total)}</span><div><button className="button secondary" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - pageSize))}><Icon name="left" size={15}/>Назад</button><button className="button secondary" disabled={offset + result.items.length >= result.total} onClick={() => setOffset(value => value + pageSize)}>Далее<Icon name="chevron" size={15}/></button></div></div>
      </>}
      <p className="patterns-disclaimer">События не меняют приоритет проверки автоматически. Совпадение дат и связей не подтверждает нарушение или движение одних и тех же средств.</p>
    </>}
  </section>;
}

function PatternEvidence({ pattern, selectedGid, openClient }: { pattern: Pattern; selectedGid: string; openClient: (gid: string) => void }) {
  const subjectLabel = pattern.kind === "group_receipts" || pattern.kind === "repeated_group" ? "Получатель" : pattern.kind === "repeated_route" || pattern.kind === "transit_window" ? "Промежуточный клиент" : "Клиент события";
  const matches = Array.isArray(pattern.measurements.matches) ? pattern.measurements.matches.filter((value): value is { incoming_date: string; outgoing_date: string; matched_kzt: number } => typeof value === "object" && value !== null && "incoming_date" in value && "outgoing_date" in value && "matched_kzt" in value) : [];
  const occurrences = dailyMeasurements(pattern.measurements.occurrences);
  const baseline = dailyMeasurements(pattern.measurements.baseline);
  return <>
    {pattern.focus_gid !== selectedGid && <p className="pattern-subject">{subjectLabel}: <button className="table-link" onClick={() => openClient(pattern.focus_gid)}>{pattern.focus_gid}</button>. Выбранный клиент участвует в этом событии.</p>}
    {typeof pattern.measurements.temporal_status === "string" && <p className="pattern-chronology">{valueLabels[pattern.measurements.temporal_status] || pattern.measurements.temporal_status}</p>}
    {pattern.episodes.length > 0 && <div className="pattern-episodes">{pattern.episodes.map((episode, index) => <div className="pattern-episode" key={index}><h4>Эпизод {index + 1}</h4><ol>{episode.steps.map((step, stepIndex) => <li key={`${stepIndex}-${step.src}-${step.dst}-${step.date}`}><span className="episode-step-number">{stepIndex + 1}</span><div><div className="episode-gids"><button className="table-link" onClick={() => openClient(step.src)}>{step.src}</button><Icon name="arrow" size={16}/><button className="table-link" onClick={() => openClient(step.dst)}>{step.dst}</button></div><p>{shortDate(step.date)} · {formatNumber(step.sum_kzt, 2)} KZT · операций: {formatNumber(step.n_tx)}</p></div></li>)}</ol></div>)}</div>}
    {pattern.gids.length > 0 && !pattern.episodes.length && <div className="pattern-participants"><span>Участники</span><div>{pattern.gids.map(gid => <button className="table-link" key={gid} onClick={() => openClient(gid)}>{gid}</button>)}</div></div>}
    {occurrences.length > 0 && <DailyMeasurements title="Даты повторения состава" items={occurrences}/>}
    {baseline.length > 0 && <DailyMeasurements title="Другие активные дни для сравнения" items={baseline}/>}
    {matches.length > 0 && <details className="pattern-transactions"><summary>Сопоставление входа и выхода · {matches.length}</summary><p>FIFO распределяет входящий объём один раз в пределах этого клиента. Это сопоставление сумм, а не доказательство происхождения средств.</p><div className="table-scroll"><table><thead><tr><th>Дата входа</th><th>Дата выхода</th><th>Сопоставлено · KZT</th></tr></thead><tbody>{matches.map((match, index) => <tr key={index}><td>{shortDate(match.incoming_date)}</td><td>{shortDate(match.outgoing_date)}</td><td>{formatNumber(match.matched_kzt, 2)}</td></tr>)}</tbody></table></div></details>}
    <PatternParameters measurements={pattern.measurements} rule={pattern.rule}/>
    {pattern.limitations.length > 0 && <div className="pattern-limitations"><h4>Ограничения вывода</h4><ul>{pattern.limitations.map(item => <li key={item}>{item}</li>)}</ul></div>}
  </>;
}

const parameterLabels: Record<string, string> = {
  temporal_share: "Доля совместимого объёма", matched_days: "Дней с сопоставленным выходом", occurrence_count: "Дат повторения", payer_gids: "Общие плательщики",
  count_baseline: "Медиана числа операций", amount_baseline: "Медиана суммы · KZT", baseline_days: "Других активных дней", triggered_metrics: "Сработавшие признаки", min_other_days: "Минимум других активных дней", min_count: "Минимум операций для всплеска количества", amount_min_count: "Минимум операций для всплеска суммы", period: "Период правила", matching: "Способ сопоставления", route_length: "Рёбер в маршруте", cycle_max_length: "Максимум рёбер цикла", edge_day_reuse_within_route: "Повторное использование дневного ребра", temporal_evaluation_complete: "Проверка дат завершена", repeat_count_is_lower_bound: "Число повторов — нижняя оценка", evidence_scope: "Область исходных операций", incoming_total_kzt: "Наблюдаемый вход · KZT", outgoing_total_kzt: "Наблюдаемый выход · KZT",
  direction: "Направление", n_tx: "Операций", sum_kzt: "Сумма · KZT", count_ratio: "Отношение числа операций к медиане", amount_ratio: "Отношение суммы к медиане",
  median_n_tx: "Медиана операций в другие активные дни", median_sum_kzt: "Медиана суммы в другие активные дни · KZT", other_active_days: "Других активных дней",
  n_payers: "Плательщиков", payer_count: "Плательщиков", n_counterparties: "Контрагентов", counterparty_count: "Контрагентов", shared_payer_count: "Общих плательщиков", n_dates: "Дат", dates: "Даты", repeat_count: "Повторов", episode_count: "Эпизодов", n_episodes: "Эпизодов", cycle_length: "Длина цикла", matched_kzt: "Совместимый объём · KZT", matched_share: "Доля совместимого объёма", daily_share: "Доля месячного потока", min_other_active_days: "Минимум других активных дней", min_payers: "Минимум плательщиков", min_repeats: "Минимум повторов", window_days: "Окно, дней", multiplier: "Множитель к медиане", min_n_tx: "Минимум операций", min_lag_days: "Минимум дней между шагами", max_lag_days: "Максимум дней между шагами", min_episodes: "Минимум эпизодов", min_cycle_length: "Минимум рёбер цикла", max_cycle_length: "Максимум рёбер цикла", chronology: "Порядок дат", temporal_status: "Порядок дат", status: "Результат проверки",
};
const valueLabels: Record<string, string> = { incoming: "Входящие", outgoing: "Исходящие", in: "Входящие", out: "Исходящие", strict: "Строго последовательные даты", strictly_increasing: "Строго последовательные даты", chronological: "Найдена последовательность по датам", date_order_unknown: "Порядок операций внутри дня неизвестен", same_day_order_unknown: "Порядок внутри дня неизвестен", structural_only: "Только структура связей: последовательный эпизод не найден", not_evaluated: "Проверка дат не завершена", insufficient_history: "Недостаточно истории", true: "Да", false: "Нет", count: "Количество операций", amount: "Сумма", calendar_day: "Календарный день", common_payer_core_on_distinct_dates: "Общий состав плательщиков на разных датах", daily_fifo: "Дневной FIFO: сначала более ранний вход", selected_episodes: "Выбранные эпизоды", selected_episode: "Один опорный эпизод", full_observed_period: "Весь наблюдаемый период" };
function parameterValue(value: unknown): string {
  if (typeof value === "number") return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  if (typeof value === "string") return valueLabels[value] || (/^\d{4}-\d{2}-\d{2}$/.test(value) ? shortDate(value) : value);
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (Array.isArray(value) && value.every(item => ["string", "number"].includes(typeof item))) return value.map(parameterValue).join(", ");
  return "";
}
function PatternParameters({ measurements, rule }: { measurements: Record<string, unknown>; rule: Record<string, unknown> }) {
  const measured = Object.entries(measurements).filter(([key, value]) => parameterLabels[key] && parameterValue(value));
  const thresholds = Object.entries(rule).filter(([key, value]) => parameterLabels[key] && parameterValue(value));
  if (!measured.length && !thresholds.length) return null;
  return <details className="pattern-parameters"><summary>Измерения и условия правила</summary>{measured.length > 0 && <dl>{measured.map(([key, value]) => <div key={key}><dt>{parameterLabels[key]}</dt><dd>{parameterValue(value)}</dd></div>)}</dl>}{thresholds.length > 0 && <><h4>Пороги детектора</h4><dl>{thresholds.map(([key, value]) => <div key={key}><dt>{parameterLabels[key]}</dt><dd>{parameterValue(value)}</dd></div>)}</dl></>}</details>;
}

type DailyMeasurement = { date: string; n_tx: number; sum_kzt: number };
function dailyMeasurements(value: unknown): DailyMeasurement[] {
  return Array.isArray(value) ? value.filter((item): item is DailyMeasurement => typeof item === "object" && item !== null && typeof item.date === "string" && typeof item.n_tx === "number" && typeof item.sum_kzt === "number") : [];
}
function DailyMeasurements({ title, items }: { title: string; items: DailyMeasurement[] }) {
  return <details className="pattern-transactions"><summary>{title} · {items.length}</summary><div className="table-scroll"><table><thead><tr><th>Дата</th><th>Операций</th><th>Сумма · KZT</th></tr></thead><tbody>{items.map(item => <tr key={item.date}><td>{shortDate(item.date)}</td><td>{formatNumber(item.n_tx)}</td><td>{formatNumber(item.sum_kzt, 2)}</td></tr>)}</tbody></table></div></details>;
}
function TransactionEvidence({ title, transactions, openClient }: { title: string; transactions: PatternDetail["transactions"]; openClient: (gid: string) => void }) {
  const chronological = [...transactions].sort((left, right) => left.date.localeCompare(right.date) || left.src.localeCompare(right.src) || left.dst.localeCompare(right.dst) || left.ref.localeCompare(right.ref));
  return <details className="pattern-transactions"><summary>{title} · {formatNumber(transactions.length)}</summary><p>Строки отсортированы по дате; порядок внутри дня неизвестен. Ссылки относятся к строкам текущей выгрузки. Одинаковые строки сохранены как отдельные операции.</p><div className="table-scroll"><table><thead><tr><th>Дата</th><th>Плательщик · gid</th><th>Получатель · gid</th><th>Сумма · KZT</th><th>Ссылка на строку</th></tr></thead><tbody>{chronological.map(tx => <tr key={tx.ref}><td>{shortDate(tx.date)}</td><td><button className="table-link" onClick={() => openClient(tx.src)}>{tx.src}</button></td><td><button className="table-link" onClick={() => openClient(tx.dst)}>{tx.dst}</button></td><td>{formatNumber(tx.sum_kzt, 2)}</td><td className="evidence-ref">{tx.ref}</td></tr>)}</tbody></table></div></details>;
}
