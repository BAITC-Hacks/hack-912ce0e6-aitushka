"use client";

import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/icon";
import { GraphPatternSelection, Pattern, request } from "@/lib/types";

type Claim = { text: string; refs: string[] };
type Source = { id: string; label: string; data: Record<string, unknown>; pattern?: { pattern_id: string; title: string; summary: string } };
type Answer = {
  answer: { insufficient_data: boolean; observations: Claim[]; hypotheses: Claim[]; limitations: Claim[]; next_steps: Claim[] };
  sources: Source[]; scope: { total_events: number; included_events: number; selected_event: boolean; events_truncated: boolean; daily_truncated: boolean };
  fingerprint: string; gid: string; model: string; cached: boolean; created_at: string; mode: string;
};
const sections = [{ key: "observations", title: "Что наблюдается" }, { key: "hypotheses", title: "Возможное объяснение" },
  { key: "limitations", title: "Что пока неизвестно" }, { key: "next_steps", title: "Что проверить дальше" }] as const;
const suggestions = ["Почему клиент в приоритете?", "Какие ограничения влияют на вывод?", "Что запросить для дальнейшей проверки?"];
const errorText = (error: unknown) => error instanceof TypeError ? "Нет соединения с сервером. Повторите запрос позже." : error instanceof Error ? error.message : "Не удалось получить ответ AI.";

function downloadBrief(answer: Answer) {
  const lines = [`ЧЕРНОВИК AI · клиент ${answer.gid}`, `Модель: ${answer.model}`, `Расчёт: ${answer.fingerprint}`,
    "Интерпретацию необходимо проверить по исходным данным.", ""];
  for (const section of sections) {
    lines.push(section.title);
    for (const claim of answer.answer[section.key]) lines.push(`${claim.text} [${claim.refs.join(", ")}]`);
    lines.push("");
  }
  lines.push("ОСНОВАНИЯ");
  for (const source of answer.sources) lines.push(`${source.id} · ${source.label}\n${JSON.stringify(source.data, null, 2)}\n`);
  const url = URL.createObjectURL(new Blob(["\ufeff" + lines.join("\n")], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `ai_brief_${answer.gid}.txt`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function AnalystAI({ gid, fingerprint, pattern, openPattern }: {
  gid: string; fingerprint: string; pattern?: Pattern; openPattern: (selection: GraphPatternSelection) => void;
}) {
  const id = useId();
  const [status, setStatus] = useState<{ configured: boolean; model: string } | null>(null);
  const [statusError, setStatusError] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [asked, setAsked] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeSource, setActiveSource] = useState<Source | null>(null);
  const controller = useRef<AbortController | null>(null);
  const sourceHeading = useRef<HTMLHeadingElement>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const task = new AbortController();
    request<{ configured: boolean; model: string }>("/api/ai/status", task.signal)
      .then(value => { if (!task.signal.aborted) setStatus(value); })
      .catch(cause => { if (!task.signal.aborted) setStatusError(errorText(cause)); });
    return () => { task.abort(); controller.current?.abort(); };
  }, []);
  useEffect(() => { if (activeSource) sourceHeading.current?.focus(); }, [activeSource]);
  useEffect(() => { if (answer) resultHeading.current?.focus({ preventScroll: true }); }, [answer]);

  async function ask(mode: "explain" | "question" | "brief", value = question) {
    if (controller.current || !fingerprint || (mode === "question" && !value.trim())) return;
    const task = new AbortController(); controller.current = task;
    setLoading(true); setError(""); setAnswer(null); setActiveSource(null);
    setAsked(mode === "explain" ? (pattern ? "Объяснение события" : "Объяснение клиента") : mode === "brief" ? "Черновик заключения" : value.trim());
    try {
      const response = await fetch(`/api/clients/${encodeURIComponent(gid)}/ai`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: task.signal,
        body: JSON.stringify({ fingerprint, mode, question: mode === "question" ? value.trim() : "", pattern_id: pattern?.pattern_id ?? null }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось получить объяснение. Обновите карточку и повторите запрос.");
      if (!task.signal.aborted) setAnswer(data);
    } catch (cause) { if (!task.signal.aborted) setError(errorText(cause)); }
    finally { if (!task.signal.aborted) { setLoading(false); controller.current = null; } }
  }

  function cancel() { controller.current?.abort(); controller.current = null; setLoading(false); setAsked(""); }
  const disabled = loading || !fingerprint;
  function submit(event: FormEvent) { event.preventDefault(); void ask("question"); }

  return <section className={`ai-panel ${pattern ? "ai-event" : "card"}`} aria-labelledby={`${id}-title`}>
    <div className="ai-heading"><div><span className="ai-eyebrow">AI · ПОМОЩНИК АНАЛИТИКА</span><h2 id={`${id}-title`}>{pattern ? "Разобрать событие с AI" : "Понять главное и выбрать следующий шаг"}</h2><p>{pattern ? "Объяснение выбранного события с учётом его дат и ограничений." : "Объяснение клиента, вопросы по данным и черновик заключения с основаниями."}</p></div><span className="ai-badge">{status?.configured ? status.model : "По запросу"}</span></div>
    <div className="ai-actions"><button className="button dark" disabled={disabled} onClick={() => void ask("explain")}><Icon name="info" size={17}/>{pattern ? "Объяснить событие" : "Объяснить клиента"}</button>{!pattern && <button className="button secondary" disabled={disabled} onClick={() => void ask("brief")}>Подготовить заключение</button>}</div>
    {!pattern && <div className="ai-suggestions">{suggestions.map(value => <button key={value} disabled={disabled} onClick={() => { setQuestion(value); void ask("question", value); }}>{value}</button>)}</div>}
    <form className="ai-form" onSubmit={submit}><label htmlFor={`${id}-question`}>Вопрос {pattern ? "об этом событии" : "по текущему клиенту"}</label><div><textarea id={`${id}-question`} maxLength={1500} rows={2} value={question} onChange={event => setQuestion(event.target.value)} placeholder="Что подтверждают операции и каких данных не хватает?" disabled={loading}/><button className="button secondary" disabled={disabled || !question.trim()} type="submit"><Icon name="arrow" size={17}/>Спросить</button></div></form>
    <p className="ai-disclosure">По нажатию отправим в OpenAI вопрос и выбранные показатели, даты и события. Идентификаторы заменяются обозначениями участников. AI может ошибаться — проверяйте интерпретацию по основаниям.</p>
    {(statusError || status?.configured === false) && <p className="ai-notice">{statusError || "Ключ AI пока не настроен на сервере. Обычная справка и вся аналитика доступны."}</p>}
    {loading && <div className="ai-loading" role="status"><span className="spinner"/><span>Анализируем основания… Это может занять до минуты.</span><button className="text-button" onClick={cancel}>Отменить ожидание</button></div>}
    {error && <div className="notice error" role="alert"><div><strong>AI сейчас недоступен</strong><p>{error}</p><p>Можно продолжить проверку по событиям или скачать обычную справку ниже.</p></div></div>}
    {answer && <div className="ai-answer"><div className="ai-answer-heading"><div><h3 ref={resultHeading} tabIndex={-1}>{asked}</h3><span>{answer.cached ? "Сохранённый ответ" : "Новый ответ"} · AI-черновик</span></div><button className="button secondary" onClick={() => downloadBrief(answer)}><Icon name="download" size={16}/>Скачать с основаниями</button></div>
      {answer.answer.insufficient_data && <p className="ai-notice">Для полного ответа недостаточно данных. Ниже указано, что можно установить и что нужно уточнить.</p>}
      {(answer.scope.events_truncated || answer.scope.daily_truncated) && <p className="ai-notice">Передана часть контекста: событий {answer.scope.included_events} из {answer.scope.total_events}{answer.scope.daily_truncated ? ", дневная история сокращена" : ""}. Ответ не охватывает всю доступную историю.</p>}
      <div className="ai-sections">{sections.map(section => answer.answer[section.key].length > 0 && <div key={section.key}><h4>{section.title}</h4>{answer.answer[section.key].map((claim, index) => <div className="ai-claim" key={index}><p>{claim.text}</p><div className="ai-citations">{claim.refs.map(ref => <button key={ref} onClick={() => setActiveSource(answer.sources.find(source => source.id === ref) || null)} aria-label={`Показать основание ${ref}`}>{ref}<Icon name="upRight" size={12}/></button>)}</div></div>)}</div>)}</div>
      {activeSource && <div className="ai-source"><div className="ai-source-heading"><h4 ref={sourceHeading} tabIndex={-1}>{activeSource.id} · {activeSource.label}</h4><button className="text-button" onClick={() => setActiveSource(null)}>Закрыть основание</button></div><SourceData value={activeSource.data}/>{activeSource.pattern && <button className="button secondary" onClick={() => openPattern({ ...activeSource.pattern!, fingerprint: answer.fingerprint })}><Icon name="graph" size={16}/>Открыть событие на графе</button>}</div>}
      <p className="ai-disclosure">Ссылки проверены на наличие в текущем расчёте. Это не гарантирует правильность интерпретации модели. Расчёт {answer.fingerprint.slice(0, 12)}.</p>
    </div>}
  </section>;
}

const fieldLabels: Record<string, string> = { period: "Период", from: "С", to: "По", limitations: "Ограничения", client_observation: "Контекст клиента", method: "Методика", gid: "Клиент", role: "Роль", role_score: "Сила признаков", priority_score: "Приоритет", evidence: "Основания роли", why: "Основания приоритета", alternative_role: "Альтернативная роль", contributions: "Вклады в приоритет", in_kzt: "Вход · KZT", out_kzt: "Выход · KZT", in_tx: "Входящих операций", out_tx: "Исходящих операций", in_deg: "Плательщиков", out_deg: "Получателей", seed_reach: "Seed выше по цепочке", depth: "Глубина", is_seed: "Seed", days: "Дневные потоки", date: "Дата", src: "Отправитель", dst: "Получатель", sum_kzt: "Сумма · KZT", n_tx: "Операций", summary: "Наблюдение", measurements: "Измерения", rule: "Правило", episodes: "Эпизоды", steps: "Шаги", focus_gid: "Основной клиент события", recommendations: "Рекомендации", incoming_top5: "Основные плательщики", outgoing_top5: "Основные получатели", temporal_status: "Проверка дат", chronological: "Последовательность по датам", date_order_unknown: "Порядок одного дня неизвестен", structural_only: "Только структурное замыкание", not_evaluated: "Проверка не завершена", date_from: "Начало", date_to: "Окончание", source_operation_count: "Исходных операций" };
Object.assign(fieldLabels, { temporal_matched_kzt: "Совместимый по датам объём · KZT", temporal_share: "Доля совместимого объёма (0–1)", total_events: "Всего событий", total_active_days: "Всего активных дней", truncated: "История сокращена", included_events: "Передано событий", coverage: "Полнота поиска", status: "Статус анализа", kind_counts: "Количество по типам", total_episodes: "Всего эпизодов", kind: "Тип события" });
function SourceData({ value }: { value: unknown }) {
  if (Array.isArray(value)) return <ol className="ai-source-list">{value.map((item, index) => <li key={index}><SourceData value={item}/></li>)}</ol>;
  if (value && typeof value === "object") return <dl className="ai-source-data">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{fieldLabels[key] || key.replaceAll("_", " ")}</dt><dd><SourceData value={item}/></dd></div>)}</dl>;
  return <span>{typeof value === "boolean" ? (value ? "Да" : "Нет") : value == null ? "Нет данных" : typeof value === "number" ? value.toLocaleString("ru-RU", { maximumFractionDigits: 6 }) : fieldLabels[String(value)] || String(value)}</span>;
}
