"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/icon";
import { Client, ClientDetails, ClientList, Edge, Filters, Overview, View, colors, emptyFilters, filterParams, formatMoney, formatNumber, labels, request, shortDate } from "@/lib/types";

const navigation: { key: View; label: string; icon: string }[] = [
  { key: "overview", label: "Обзор сети", icon: "overview" },
  { key: "client", label: "Карточка клиента", icon: "client" },
  { key: "graph", label: "Граф связей", icon: "graph" },
  { key: "exports", label: "Выгрузки", icon: "download" },
];
const headings: Record<View, [string, string, string]> = {
  overview: ["Обзор", "сети.", "Выберите клиента, изучите основания и проследите переводы."],
  client: ["Карточка", "клиента.", "Почему клиент в приоритете, с кем связан и что стоит проверить."],
  graph: ["Карта", "связей.", "От кого приходят средства и куда уходят дальше."],
  exports: ["Результаты", "анализа.", "Скачайте данные для проверки и передачи команде."],
};
const friendlyError = (error: unknown) => error instanceof TypeError ? "Нет соединения с аналитическим сервером. Проверьте запуск приложения и повторите попытку." : error instanceof Error ? error.message : "Не удалось загрузить данные. Повторите попытку.";

export default function Workspace() {
  const [view, setView] = useState<View>("overview");
  const [urlReady, setUrlReady] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [list, setList] = useState<ClientList>({ items: [], total: 0 });
  const [selected, setSelected] = useState("");
  const [details, setDetails] = useState<ClientDetails | null>(null);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [showFilters, setShowFilters] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [overviewError, setOverviewError] = useState("");
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [mobileNav, setMobileNav] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [graphMode, setGraphMode] = useState("ego");
  const [hops, setHops] = useState("1");
  const [colorBy, setColorBy] = useState("role");
  const [fullGraph, setFullGraph] = useState(false);
  const [graphReady, setGraphReady] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [queueNav, setQueueNav] = useState<{ position: number | null; total: number; previous_gid: string | null; next_gid: string | null } | null>(null);
  const searchRequest = useRef<AbortController | null>(null);
  const moreRequest = useRef<AbortController | null>(null);
  const queueVersion = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const graphRef = useRef<HTMLIFrameElement>(null);
  const pageHeading = useRef<HTMLHeadingElement>(null);
  const filterKey = useMemo(() => filterParams(filters).toString(), [filters]);
  const activeCount = filters.roles.length + filters.clusters.length + Number(filters.seed !== "all") + Number(filters.boundary !== "all");

  const openClient = useCallback((gid: string, target: View = "client") => {
    searchRequest.current?.abort(); setSearching(false);
    const previousGid = selectedRef.current;
    if (previousGid && previousGid !== gid) setHistory(previous => [...previous, previousGid].slice(-30));
    setSelected(gid); setView(target); setSearchError(""); setMobileNav(false);
    window.scrollTo({ top: 0, behavior: "instant" });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialView = params.get("view"); const initialGid = params.get("gid");
    if (navigation.some(item => item.key === initialView)) setView(initialView as View);
    if (initialGid && /^-?\d+$/.test(initialGid)) setSelected(initialGid);
    setSidebarCollapsed(window.localStorage.getItem("money-graph-sidebar-collapsed") === "true");
    setUrlReady(true);
  }, []);

  useEffect(() => {
    if (!urlReady) return;
    const params = new URLSearchParams({ view });
    if (selected && (view === "client" || view === "graph")) params.set("gid", selected);
    window.history.replaceState(null, "", `?${params}`);
  }, [view, selected, urlReady]);

  useEffect(() => {
    const controller = new AbortController();
    setOverviewError("");
    request<Overview>("/api/overview", controller.signal).then(setOverview).catch(error => { if (!controller.signal.aborted) setOverviewError(friendlyError(error)); });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    queueVersion.current += 1;
    moreRequest.current?.abort(); moreRequest.current = null; setMoreLoading(false);
    setListLoading(true); setListError("");
    request<ClientList>(`/api/clients?${filterKey}&limit=50&offset=0`, controller.signal).then(data => {
      setList(data); setSelected(previous => previous || data.items[0]?.gid || "");
    }).catch(error => { if (!controller.signal.aborted) setListError(friendlyError(error)); }).finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [filterKey, refresh]);

  useEffect(() => {
    if (!selected || (view !== "client" && view !== "graph")) return;
    const controller = new AbortController();
    setDetailLoading(true); setDetailError("");
    request<ClientDetails>(`/api/clients/${encodeURIComponent(selected)}`, controller.signal).then(setDetails).catch(error => { if (!controller.signal.aborted) setDetailError(friendlyError(error)); }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected, view, refresh]);

  useEffect(() => {
    if (!selected || (view !== "client" && view !== "graph")) return;
    const controller = new AbortController();
    setQueueNav(null);
    request<NonNullable<typeof queueNav>>(`/api/clients/${encodeURIComponent(selected)}/navigation?${filterKey}`, controller.signal)
      .then(setQueueNav).catch(() => { /* Client details remain available without queue context. */ });
    return () => controller.abort();
  }, [selected, filterKey, view, refresh]);

  useEffect(() => () => { searchRequest.current?.abort(); moreRequest.current?.abort(); }, []);

  useEffect(() => {
    function receive(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.source !== graphRef.current?.contentWindow) return;
      if (event.data?.type === "money-graph:open-client" && typeof event.data.gid === "string" && /^-?\d+$/.test(event.data.gid)) openClient(event.data.gid);
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [openClient]);

  const graphSource = useMemo(() => {
    if (!selected) return "";
    const params = new URLSearchParams(filterKey);
    params.set("gid", selected); params.set("mode", graphMode); params.set("hops", hops); params.set("color_by", colorBy); params.set("full", String(fullGraph));
    params.set("revision", String(refresh));
    return `/api/graph?${params}`;
  }, [selected, graphMode, hops, colorBy, fullGraph, filterKey, refresh]);
  useEffect(() => { setGraphReady(false); setGraphError(""); }, [graphSource]);

  async function search(event: FormEvent) {
    event.preventDefault();
    const gid = query.trim();
    if (!/^-?\d+$/.test(gid) || gid.length > 20) { setSearchError("Введите полный числовой gid клиента (до 20 символов)."); return; }
    searchRequest.current?.abort();
    const controller = new AbortController(); searchRequest.current = controller;
    setSearching(true); setSearchError("");
    try {
      const data = await request<ClientDetails>(`/api/clients/${encodeURIComponent(gid)}`, controller.signal);
      if (!controller.signal.aborted) { setDetails(data); openClient(gid); }
    } catch (error) { if (!controller.signal.aborted) setSearchError(friendlyError(error)); }
    finally { if (searchRequest.current === controller) { setSearching(false); searchRequest.current = null; } }
  }

  async function loadMore() {
    if (moreRequest.current) return;
    const controller = new AbortController(); moreRequest.current = controller;
    const version = queueVersion.current;
    setMoreLoading(true);
    try {
      const data = await request<ClientList>(`/api/clients?${filterKey}&limit=50&offset=${list.items.length}`, controller.signal);
      if (!controller.signal.aborted && version === queueVersion.current) setList(previous => ({ ...data, items: [...previous.items, ...data.items] }));
    } catch (error) { if (!controller.signal.aborted && version === queueVersion.current) setListError(friendlyError(error)); }
    finally { if (moreRequest.current === controller) { setMoreLoading(false); moreRequest.current = null; } }
  }

  function navigate(next: View) {
    searchRequest.current?.abort(); setSearching(false); setSearchError("");
    setView(next); setMobileNav(false);
    window.scrollTo({ top: 0, behavior: "instant" });
    requestAnimationFrame(() => pageHeading.current?.focus());
  }
  function toggleSidebar() {
    setSidebarCollapsed(previous => {
      const next = !previous;
      window.localStorage.setItem("money-graph-sidebar-collapsed", String(next));
      return next;
    });
  }
  function goBackClient() {
    const previous = history[history.length - 1];
    if (!previous) return;
    searchRequest.current?.abort(); setSearching(false);
    setHistory(items => items.slice(0, -1)); setSelected(previous); setView("client");
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function graphLoaded() {
    setGraphReady(true);
    try {
      if (!graphRef.current?.contentDocument?.getElementById("network")) setGraphError("Граф не загрузился. Повторите запрос или проверьте доступность аналитического сервера.");
    } catch { setGraphError("Не удалось открыть граф. Повторите загрузку."); }
  }
  const client = details?.client;
  const summary = overview?.summary;
  const visibleQueue = showAll ? list.items : list.items.slice(0, 5);
  const [headline, accent, subtitle] = headings[view];

  return <div className="workspace">
    <a className="skip-link" href="#main">Перейти к содержимому</a>
    {mobileNav && <button className="nav-scrim" aria-label="Закрыть меню" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""} ${mobileNav ? "open" : ""}`}>
      <div className="sidebar-head"><a href="?view=overview" className="brand" aria-label="Граф денег — обзор" onClick={event => { event.preventDefault(); navigate("overview"); }}><span className="brand-mark"><Icon name="graph" size={24}/></span><span>Граф денег<small>АНАЛИТИКА СЕТИ</small></span></a><button className="sidebar-toggle" onClick={toggleSidebar} aria-label={sidebarCollapsed ? "Развернуть меню" : "Свернуть меню"} aria-expanded={!sidebarCollapsed} title={sidebarCollapsed ? "Развернуть меню" : "Свернуть меню"}><Icon name={sidebarCollapsed ? "chevron" : "left"} size={17}/></button></div>
      <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav aria-label="Главное меню">{navigation.map(item => <button key={item.key} className={`nav-button ${view === item.key ? "active" : ""}`} aria-current={view === item.key ? "page" : undefined} aria-label={item.label} title={sidebarCollapsed ? item.label : undefined} onClick={() => navigate(item.key)}><Icon name={item.icon}/><span>{item.label}</span>{view === item.key && <span className="nav-active-dot"/>}</button>)}</nav>
      <div className="sidebar-bottom" title="Данные обрабатываются локально"><span className="local-dot"/><span>Данные обрабатываются локально</span></div>
    </aside>

    <main id="main" className="main-shell">
      <header className="page-header"><div className="page-heading-row"><button className="mobile-menu icon-button" aria-label="Открыть меню" aria-expanded={mobileNav} onClick={() => setMobileNav(true)}><Icon name="menu"/></button><div><h1 ref={pageHeading} tabIndex={-1}>{headline} <span>{accent}</span></h1><p className="subtitle">{subtitle}</p></div></div><button className="icon-button refresh-button" onClick={() => setRefresh(value => value + 1)} aria-label="Обновить данные" title="Обновить данные"><Icon name="refresh"/></button></header>
      <div className="context-line"><span className="status-dot"/>{summary ? `${shortDate(summary.date_min)} — ${shortDate(summary.date_max)}` : "Загружаем период наблюдения"}<span className="context-divider"/>{summary ? `${formatNumber(summary.n_transactions)} операций` : "Проверка данных"}</div>

      <div className="search-filter-row"><form className="search-form" onSubmit={search}><Icon name="search" size={18}/><input aria-label="Поиск клиента по gid" placeholder="Найти клиента по полному gid…" value={query} onChange={event => setQuery(event.target.value)} inputMode="numeric" autoComplete="off"/><button type="submit" className="search-submit" disabled={searching}>{searching ? "Ищем…" : "Найти"}<Icon name="arrow" size={15}/></button></form><button className={`filter-button ${activeCount ? "has-filters" : ""}`} onClick={() => setShowFilters(true)}><Icon name="filter" size={17}/>Фильтры{activeCount > 0 && <span>{activeCount}</span>}</button></div>
      {searchError && <div role="alert" className="notice error compact"><Icon name="info" size={16}/>{searchError}</div>}
      {activeCount > 0 && <div className="filter-chips"><span>Текущий срез</span>{filters.roles.map(role => <button key={role} onClick={() => setFilters(previous => ({ ...previous, roles: previous.roles.filter(value => value !== role) }))}>{labels[role]}<Icon name="close" size={11}/></button>)}{filters.clusters.map(cluster => <button key={cluster} onClick={() => setFilters(previous => ({ ...previous, clusters: previous.clusters.filter(value => value !== cluster) }))}>Сообщество {cluster}<Icon name="close" size={11}/></button>)}{filters.seed !== "all" && <button onClick={() => setFilters(previous => ({ ...previous, seed: "all" }))}>{filters.seed === "seed" ? "Только seed" : "Без seed"}<Icon name="close" size={11}/></button>}{filters.boundary !== "all" && <button onClick={() => setFilters(previous => ({ ...previous, boundary: "all" }))}>{filters.boundary === "boundary" ? "Граница выгрузки" : "Внутри границы"}<Icon name="close" size={11}/></button>}<button className="reset-filter" onClick={() => setFilters(emptyFilters)}>Сбросить всё</button></div>}
      {overviewError && <ErrorPanel message={overviewError} retry={() => setRefresh(value => value + 1)}/>}

      {view === "overview" && <>
        <div className="metrics-grid"><Metric label="Клиентов в сети" value={summary ? formatNumber(summary.n_nodes) : "—"} detail="Все участники выборки" icon="client" blue/><Metric label="Направленных связей" value={summary ? formatNumber(summary.n_edges) : "—"} detail="Уникальные пары переводов" icon="graph"/><Metric label="Наблюдаемый оборот" value={summary ? formatMoney(summary.sum_kzt) : "—"} detail="KZT · за весь период" icon="upRight"/><Metric label="Сообществ" value={summary ? formatNumber(summary.n_clusters) : "—"} detail="Группы связанных клиентов" icon="layers"/></div>
        <div className="overview-grid"><section className="card queue-card"><div className="section-heading"><div><h2>Приоритет проверки</h2><p>Клиенты, требующие внимания в первую очередь</p></div><span className="count-pill">{formatNumber(list.total)} в срезе</span></div>
          {listLoading ? <QueueSkeleton/> : listError ? <ErrorPanel message={listError} retry={() => setRefresh(value => value + 1)}/> : list.total === 0 ? <EmptyState title="В этом срезе нет клиентов" description="Измените фильтры или найдите любой gid через глобальный поиск." action={() => setFilters(emptyFilters)} actionLabel="Сбросить фильтры"/> : <>
            <div className="queue-labels"><span>КЛИЕНТ / ГИПОТЕЗА РОЛИ</span><span>ПРИОРИТЕТ</span></div>
            <div className="queue-list">{visibleQueue.map((node, index) => <button className="queue-row" key={node.gid} onClick={() => openClient(node.gid)} title={node.why}><span className={`queue-rank ${index === 0 ? "first" : ""}`}>{String(index + 1).padStart(2, "0")}</span><span className="queue-person"><strong>{node.gid}</strong><span><i className="role-dot" style={{ background: colors[node.role] }}/>{labels[node.role]}{node.truncated_by_depth && <i className="boundary-mark" title="Граница выгрузки">◆</i>}</span></span><span className="queue-score">{formatNumber(node.priority_score, 3)}<span className="score-track"><i style={{ width: `${node.priority_score * 100}%` }}/></span></span><span className="queue-arrow"><Icon name="upRight" size={17}/></span></button>)}</div>
            <div className="queue-bottom"><span>Показано {visibleQueue.length} из {formatNumber(list.total)} клиентов</span><button className="text-button" onClick={() => setShowAll(value => !value)}>{showAll ? "Только первые 5" : "Вся очередь"}<Icon name="arrow" size={15}/></button></div>
            {showAll && list.items.length < list.total && <button className="button secondary load-more" onClick={loadMore} disabled={moreLoading}>{moreLoading ? "Загружаем…" : "Показать ещё 50 клиентов"}</button>}
          </>}
          <div className="queue-footnote"><Icon name="info" size={14}/><span>Оценка 0–1 помогает выбрать следующую проверку и не является вероятностью нарушения.</span></div>
        </section><div className="overview-side"><section className="card roles-card"><div className="section-heading"><div><h2>Структура сети</h2><p>Распределение ролей по всей выборке</p></div><span className="icon-disc"><Icon name="layers" size={17}/></span></div><div className="role-bars">{(overview?.roles || []).map(role => <div className="role-stat" key={role.role}><div><span><i className="role-dot" style={{ background: role.color }}/>{role.label}</span><b>{formatNumber(role.count)}</b></div><div className="role-bar-track"><i style={{ width: `${role.count / Math.max(...(overview?.roles || []).map(item => item.count), 1) * 100}%`, background: role.color }}/></div></div>)}</div></section></div></div>
        <div className="boundary-summary"><span className="boundary-symbol"><Icon name="shield" size={19}/></span><div><b>{summary ? formatNumber(summary.boundary_count) : "—"} клиентов на границе наблюдения</b><p>Дальнейшие переводы за границей выгрузки неизвестны. Отсутствие исходящих связей не означает, что средства остановились.</p></div></div>
      </>}

      {(view === "client" || view === "graph") && <>
        {!selected && !listLoading && <EmptyState title="Выберите клиента" description="Найдите gid или выберите клиента в очереди проверок." action={() => navigate("overview")} actionLabel="Перейти к обзору"/>}
        {detailLoading && <div className="loading-card" role="status"><span className="spinner"/>Загружаем клиента {selected}…</div>}
        {detailError && <ErrorPanel message={detailError} retry={() => setRefresh(value => value + 1)}/>}
        {!detailLoading && !detailError && client && client.gid === selected && <>
          <section className="card client-identity"><div className="identity-main"><span className="identity-icon"><Icon name={view === "graph" ? "graph" : "client"} size={29}/></span><div><div className="eyebrow">{view === "graph" ? "В ЦЕНТРЕ ГРАФА" : "КЛИЕНТ СЕТИ"}</div><h2 className="gid-heading">{client.gid}</h2><div className="badge-row"><span className="role-badge"><i className="role-dot" style={{ background: colors[client.role] }}/>{labels[client.role]} · гипотеза</span><span className="neutral-badge">Сообщество {client.cluster_id}</span>{client.is_seed && <span className="neutral-badge">Seed</span>}{client.truncated_by_depth && <span className="boundary-badge">◆ Граница выгрузки</span>}</div></div></div><div className="identity-actions"><button className="button dark" onClick={() => navigate(view === "client" ? "graph" : "client")}><Icon name={view === "client" ? "graph" : "client"} size={16}/>{view === "client" ? "Связи клиента" : "Карточка клиента"}</button><div className="queue-navigation"><button className="icon-button" disabled={!queueNav?.previous_gid || listLoading} aria-label="Предыдущий клиент" onClick={() => queueNav?.previous_gid && openClient(queueNav.previous_gid, view)}><Icon name="left" size={15}/></button><span>{!queueNav ? "Позиция в очереди…" : queueNav.position != null ? `${queueNav.position} из ${formatNumber(queueNav.total)} в очереди` : "Вне фильтров очереди"}</span><button className="icon-button" disabled={!queueNav?.next_gid || listLoading} aria-label="Следующий клиент" onClick={() => queueNav?.next_gid && openClient(queueNav.next_gid, view)}><Icon name="chevron" size={15}/></button></div></div></section>

          {history.length > 0 && <button className="text-button" onClick={goBackClient}><Icon name="left" size={15}/>Вернуться к предыдущему клиенту</button>}
          {view === "client" && <ClientPanel details={details!} openClient={openClient} windowDays={overview?.metadata.temporal?.window_days || 2}/>}
          {view === "graph" && <section className="card graph-card"><div className="section-heading"><div><h2>Карта наблюдаемых переводов</h2><p>Стрелки показывают направление, размер узла — приоритет</p></div><a className="button secondary" href={graphSource} download={`graph_${selected}.html`}><Icon name="download" size={15}/>HTML</a></div><div className="graph-controls"><label>Область<select value={graphMode} onChange={event => setGraphMode(event.target.value)}><option value="ego">Окружение клиента</option><option value="community">Всё сообщество клиента</option><option value="filtered">Отфильтрованная сеть</option></select></label><label>Переходы<select value={hops} onChange={event => setHops(event.target.value)} disabled={graphMode !== "ego"}><option value="1">Один переход</option><option value="2">Два перехода</option></select></label><label>Цвет узлов<select value={colorBy} onChange={event => setColorBy(event.target.value)}><option value="role">По роли</option><option value="cluster">По сообществу</option></select></label><label className="checkbox-label full-graph"><input type="checkbox" checked={fullGraph} onChange={event => setFullGraph(event.target.checked)}/>Все узлы области</label></div><div className="graph-caption">{graphMode === "filtered" ? "Применены текущие фильтры. Выбранный клиент сохранён в области." : "Окружение и сообщество включают связи независимо от фильтров."} {fullGraph ? "Большой граф может потребовать больше времени." : "До 180 узлов; число скрытых указано на графе."}</div><div className="graph-legend">{colorBy === "role" && Object.entries(labels).map(([role, label]) => <span key={role}><i style={{ background: colors[role] }}/>{label}</span>)}<span>◆ граница выгрузки</span><span>Обводка — seed / выбранный узел</span></div><div className="graph-frame-wrap">{graphError && <ErrorPanel message={graphError} retry={() => setRefresh(value => value + 1)}/>}{!graphReady && <div className="graph-loading" role="status"><span className="spinner"/>Строим граф…</div>}<iframe key={graphSource} ref={graphRef} title="Интерактивный граф переводов" src={graphSource} onLoad={graphLoaded} onError={() => { setGraphReady(true); setGraphError("Граф не загрузился. Повторите попытку."); }}/></div><p className="muted small">Нажмите на узел для деталей. Кнопка «Открыть карточку» в графе продолжит исследование выбранного клиента. Достижимость не доказывает движение одних и тех же денег.</p></section>}
        </>}
      </>}

      {view === "exports" && <Exports overview={overview}/>}
    </main>
    {showFilters && <FilterDialog filters={filters} overview={overview} apply={value => { setFilters(value); setShowFilters(false); }} close={() => setShowFilters(false)}/>}
  </div>;
}

function Metric({ label, value, detail, icon, blue }: { label: string; value: string; detail: string; icon: string; blue?: boolean }) {
  return <div className={`metric ${blue ? "metric-blue" : ""}`}><div className="metric-label">{label}<Icon name={icon} size={16}/></div><div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></div>;
}

function ErrorPanel({ message, retry }: { message: string; retry: () => void }) { return <div className="notice error" role="alert"><Icon name="info"/><div><strong>Не удалось загрузить данные</strong><p>{message}</p></div><button className="button secondary" onClick={retry}>Повторить</button></div>; }
function EmptyState({ title, description, action, actionLabel }: { title: string; description: string; action: () => void; actionLabel: string }) { return <div className="empty-state"><span className="icon-disc"><Icon name="search" size={23}/></span><h3>{title}</h3><p>{description}</p><button className="button secondary" onClick={action}>{actionLabel}</button></div>; }
function QueueSkeleton() { return <div aria-label="Загрузка очереди" role="status" className="queue-skeleton">{[1, 2, 3, 4, 5].map(i => <div key={i}><span/><i/><b/></div>)}</div>; }

function FilterDialog({ filters, overview, apply, close }: { filters: Filters; overview: Overview | null; apply: (filters: Filters) => void; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState<Filters>({ ...filters, roles: [...filters.roles], clusters: [...filters.clusters] });
  const [clusterSearch, setClusterSearch] = useState("");
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  function toggle(field: "roles" | "clusters", value: string) { setDraft(previous => ({ ...previous, [field]: previous[field].includes(value) ? previous[field].filter(item => item !== value) : [...previous[field], value] })); }
  return <dialog ref={ref} className="filter-dialog" aria-labelledby="filter-title" onCancel={close} onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close(); } }}><div className="dialog-heading"><div><div className="eyebrow">ТОЧНЕЕ ВЫБОРКА</div><h2 id="filter-title">Фильтры сети</h2></div><button className="icon-button" onClick={close} aria-label="Закрыть фильтры"><Icon name="close"/></button></div><p className="muted small">Фильтры меняют очередь и режим «Отфильтрованная сеть». Поиск gid работает по всей выборке.</p><fieldset><legend>Гипотеза роли</legend><div className="filter-role-grid">{Object.entries(labels).map(([role, label]) => <label className="checkbox-label" key={role}><input type="checkbox" checked={draft.roles.includes(role)} onChange={() => toggle("roles", role)}/><i className="role-dot" style={{ background: colors[role] }}/>{label}</label>)}</div></fieldset><fieldset><legend>Сообщества <span>{draft.clusters.length || "Все"}</span></legend><input className="cluster-search" aria-label="Поиск номера сообщества" placeholder="Номер сообщества…" value={clusterSearch} onChange={event => setClusterSearch(event.target.value)}/><div className="cluster-list">{overview?.clusters.filter(cluster => String(cluster.cluster_id).includes(clusterSearch)).map(cluster => <label key={cluster.cluster_id} className="checkbox-label"><input type="checkbox" checked={draft.clusters.includes(String(cluster.cluster_id))} onChange={() => toggle("clusters", String(cluster.cluster_id))}/><span>Сообщество {cluster.cluster_id}</span><small>{cluster.n_nodes} клиентов</small></label>)}</div></fieldset><div className="filter-selects"><label>Исходные клиенты<select value={draft.seed} onChange={event => setDraft(previous => ({ ...previous, seed: event.target.value as Filters["seed"] }))}><option value="all">Все клиенты</option><option value="seed">Только seed</option><option value="nonseed">Без seed</option></select></label><label>Наблюдаемость<select value={draft.boundary} onChange={event => setDraft(previous => ({ ...previous, boundary: event.target.value as Filters["boundary"] }))}><option value="all">Все уровни</option><option value="boundary">Граница выгрузки</option><option value="internal">Внутри границы</option></select></label></div><div className="dialog-footer"><button className="text-button" onClick={() => setDraft(emptyFilters)}>Сбросить</button><button className="button dark" onClick={() => apply(draft)}>Применить фильтры<Icon name="check" size={16}/></button></div></dialog>;
}

function downloadTransactions(details: ClientDetails) {
  const rows = ["date,src,dst,sum_kzt", ...details.transactions.map(tx => [tx.date, tx.src, tx.dst, tx.sum_kzt].join(","))];
  const url = URL.createObjectURL(new Blob(["\uFEFF", rows.join("\n") + "\n"], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `transactions_${details.client.gid}.csv`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ClientPanel({ details, openClient, windowDays }: { details: ClientDetails; openClient: (gid: string) => void; windowDays: number }) {
  const client = details.client;
  const factors = [
    ["Схождение seed-цепочек", client.contribution_seed_convergence], ["Наблюдаемый оборот", client.contribution_observed_flow],
    ["Посредничество", client.contribution_brokerage], ["Число плательщиков", client.contribution_fan_in],
  ] as const;
  return <div className="client-content"><div className="metrics-grid"><Metric label="Приоритет проверки" value={formatNumber(client.priority_score, 3)} detail="Шкала от 0 до 1" icon="upRight" blue/><Metric label="Сила признаков роли" value={formatNumber(client.role_score, 3)} detail="Эвристическая оценка" icon="shield"/><Metric label="Входящий поток · KZT" value={formatMoney(client.in_kzt)} detail={`${formatNumber(client.in_deg)} плательщиков · ${formatNumber(client.in_tx)} операций`} icon="download"/><Metric label="Исходящий поток · KZT" value={formatMoney(client.out_kzt)} detail={`${formatNumber(client.out_deg)} получателей · ${formatNumber(client.out_tx)} операций`} icon="upRight"/></div><div className="client-evidence-grid"><section className="reason-card"><div className="eyebrow">ПОЧЕМУ СТОИТ ПРОВЕРИТЬ</div><h2>Основания приоритета</h2><p>{client.why}</p><div className="factor-list">{factors.map(([label, value]) => <div className="factor-row" key={label}><span>{label}</span><div className="factor-bar"><i style={{ width: `${Math.min(100, value * 100)}%` }}/></div><b>{formatNumber(value, 3)}</b></div>)}</div><p className="muted small">Вклады суммируются. Полосы показывают долю общей шкалы от 0 до 1.</p><div className="factor-total"><span>Итоговый приоритет</span><b>{formatNumber(client.priority_score, 3)}</b></div></section><section className="limits-card"><div className="eyebrow">КОНТЕКСТ И ОГРАНИЧЕНИЯ</div><h2>Что говорят данные</h2><p className="role-evidence">{client.evidence}</p><div className="observation-note"><Icon name="info" size={17}/><p>{client.observation_note || "Выводы ограничены предоставленной выборкой, периодом и одним банком."}</p></div>{client.alternative_role && labels[client.alternative_role] && <p className="muted small">Альтернативная гипотеза: <b>{labels[client.alternative_role]}</b></p>}<p className="muted small">Роль описывает признаки поведения и не подтверждает нарушение. Метрики рассчитаны на полной сети. Данные ограничены периодом, банком, порогом суммы и глубиной выгрузки.</p></section></div><section className="card"><div className="section-heading"><div><h2>Потоки по дням</h2><p>Входящие и исходящие переводы · KZT</p></div><div className="chart-legend"><span><i/>Вход</span><span><i/>Выход</span></div></div><DailyChart daily={details.daily}/><div className="temporal-note"><Icon name="clock" size={16}/><p>Совместимый с транзитом через 1–{windowDays} дня объём: <b>{formatNumber(client.temporal_matched_kzt, 2)} KZT</b> ({formatNumber(client.temporal_share * 100, 1)}% от меньшего из входа и выхода). Порядок операций одного дня неизвестен; они не сопоставляются.</p></div></section><div className="neighbor-grid"><Counterparties title="Кто отправлял" subtitle={`${details.incoming.length} входящих связей`} edges={details.incoming} side="src" openClient={openClient}/><Counterparties title="Куда отправлял" subtitle={`${details.outgoing.length} исходящих связей`} edges={details.outgoing} side="dst" openClient={openClient}/></div><details className="detail-disclosure"><summary>Все признаки клиента <Icon name="chevron" size={15}/></summary><dl className="feature-grid">{[["Входящий поток · KZT", formatNumber(client.in_kzt, 2)], ["Исходящий поток · KZT", formatNumber(client.out_kzt, 2)], ["Seed выше по цепочке", client.seed_reach], ["Глубина наблюдения", client.depth], ["Активных дней", client.active_days], ["Других связанных сообществ", client.intercluster_degree], ["PageRank", formatNumber(client.pagerank, 6)], ["Посредничество", formatNumber(client.betweenness, 6)], ["Выход / вход", client.pass_through == null ? "Не определено: нет входа" : formatNumber(client.pass_through, 3)]].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></details><details className="detail-disclosure"><summary>Операции клиента <span>{details.transactions.length}</span><Icon name="chevron" size={15}/></summary><button className="button secondary" onClick={() => downloadTransactions(details)}><Icon name="download" size={15}/>Скачать операции клиента</button><div className="table-scroll"><table><thead><tr><th>Дата</th><th>Плательщик · gid</th><th>Получатель · gid</th><th>Сумма · KZT</th></tr></thead><tbody>{details.transactions.map((transaction, index) => <tr key={`${index}-${transaction.src}-${transaction.dst}`}><td>{shortDate(transaction.date)}</td><td><button className="table-link" onClick={() => openClient(transaction.src)}>{transaction.src}</button></td><td><button className="table-link" onClick={() => openClient(transaction.dst)}>{transaction.dst}</button></td><td>{formatNumber(transaction.sum_kzt, 2)}</td></tr>)}</tbody></table></div></details></div>;
}

function DailyChart({ daily }: { daily: ClientDetails["daily"] }) {
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(1, ...daily.flatMap(day => [day.in_kzt, day.out_kzt]));
  const hasFlows = daily.some(day => day.in_kzt || day.out_kzt);
  if (!hasFlows) return <div className="empty-chart"><Icon name="graph" size={28}/><p>У клиента нет операций в предоставленной выборке.</p><span>Клиент сохранён в анализе как изолированный узел.</span></div>;
  const W = 1000, H = 225, top = 15, bottom = 40, left = 80, right = 20;
  const x = (i: number) => left + i / Math.max(daily.length - 1, 1) * (W - left - right);
  const y = (value: number) => H - bottom - value / max * (H - top - bottom);
  const path = (key: "in_kzt" | "out_kzt") => daily.map((day, index) => `${index === 0 ? "M" : "L"}${x(index)},${y(day[key])}`).join(" ");
  return <div className="daily-chart"><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Дневные входящие и исходящие потоки в KZT">{[0, .25, .5, .75, 1].map(part => <g key={part}><line x1={left} y1={y(max * part)} x2={W - right} y2={y(max * part)} stroke="#e2e7e4" strokeDasharray={part ? "3 5" : "0"}/><text x={left - 12} y={y(max * part) + 4} textAnchor="end" fill="#8d9aa3" fontSize="11">{formatMoney(max * part)}</text></g>)}<path d={`${path("in_kzt")} L${x(daily.length - 1)},${H - bottom} L${left},${H - bottom} Z`} fill="#aec6e528"/><path d={path("in_kzt")} fill="none" stroke="#8daddd" strokeWidth="2.5"/><path d={path("out_kzt")} fill="none" stroke="#b5a78d" strokeWidth="2"/>{daily.map((day, index) => <g key={day.date}><rect x={x(index) - (W - left - right) / Math.max(daily.length, 1) / 2} y={top} width={(W - left - right) / Math.max(daily.length, 1)} height={H - bottom - top} fill="transparent" tabIndex={0} role="button" aria-label={`${shortDate(day.date)}: вход ${formatNumber(day.in_kzt, 2)}, выход ${formatNumber(day.out_kzt, 2)} KZT`} onMouseEnter={() => setActive(index)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(index)} onBlur={() => setActive(null)}/>{(index === 0 || index === daily.length - 1 || index % 7 === 0) && <text x={x(index)} y={H - 12} textAnchor="middle" fill="#8d9aa3" fontSize="11">{day.date.slice(8, 10)}.{day.date.slice(5, 7)}</text>}</g>)}{active != null && daily[active] && <><line x1={x(active)} y1={top} x2={x(active)} y2={H - bottom} stroke="#8098b9" strokeDasharray="4 4"/><circle cx={x(active)} cy={y(daily[active].in_kzt)} r="4" fill="#7e9dcc"/><circle cx={x(active)} cy={y(daily[active].out_kzt)} r="4" fill="#b5a78d"/></>}</svg><div className="chart-readout" aria-live="polite">{active != null && daily[active] ? `${shortDate(daily[active].date)} · Вход ${formatNumber(daily[active].in_kzt, 2)} KZT · Выход ${formatNumber(daily[active].out_kzt, 2)} KZT` : "Наведите курсор на день или выберите его клавишей Tab, чтобы увидеть точные суммы"}</div></div>;
}

function Counterparties({ title, subtitle, edges, side, openClient }: { title: string; subtitle: string; edges: Edge[]; side: "src" | "dst"; openClient: (gid: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const sorted = [...edges].sort((a, b) => b.sum_kzt - a.sum_kzt);
  return <section className="card counterparties"><div className="section-heading"><div><h2>{title}</h2><p>{subtitle}</p></div><span className="icon-disc"><Icon name={side === "src" ? "download" : "upRight"} size={17}/></span></div>{!edges.length ? <p className="empty-neighbors">Таких связей в наблюдаемой выборке нет.</p> : <><div className="counterparty-list">{(expanded ? sorted : sorted.slice(0, 6)).map(edge => <button key={`${edge.src}-${edge.dst}`} onClick={() => openClient(edge[side])}><span><strong>{edge[side]}</strong><small>{formatNumber(edge.n_tx)} операций</small></span><span>{formatMoney(edge.sum_kzt)}<small>KZT</small></span><Icon name="chevron" size={14}/></button>)}</div>{edges.length > 6 && <button className="text-button" onClick={() => setExpanded(value => !value)}>{expanded ? "Свернуть" : `Показать все ${edges.length}`}<Icon name="arrow" size={14}/></button>}</>}</section>;
}

function Exports({ overview }: { overview: Overview | null }) {
  const files = [
    { name: "nodes_roles.csv", title: "Роли клиентов", count: overview ? `${formatNumber(overview.summary.n_nodes)} клиентов` : "Все клиенты", description: "Роль, сила признаков, сообщество, приоритет и числовое объяснение каждого клиента." },
    { name: "clusters.csv", title: "Сообщества сети", count: overview ? `${overview.summary.n_clusters} сообществ` : "Все сообщества", description: "Состав сообществ, внутренний оборот, ключевые участники и структурная гипотеза." },
    { name: "top_nodes.csv", title: "Приоритеты проверки", count: "Топ клиентов", description: "Ранжированная очередь клиентов с приоритетом и основаниями для первоочередной проверки." },
  ];
  return <div className="exports-content"><div className="download-intro"><span className="ready-check"><Icon name="check" size={22}/></span><div><h2>Единый расчёт. Полная выборка.</h2><p>Файлы содержат всю сеть. Поиск и фильтры интерфейса не обрезают выгрузки.</p></div></div><div className="download-grid">{files.map((file, index) => <section className="card download-card" key={file.name}><div className="download-number">0{index + 1}<span>CSV</span></div><h2>{file.title}</h2><p>{file.description}</p><span className="download-count">{file.count}</span><code>{file.name}</code><a className="button dark" href={`/api/exports/${file.name}`} download><Icon name="download" size={16}/>Скачать CSV</a></section>)}</div><section className="card metadata-card"><div className="icon-disc"><Icon name="layers" size={24}/></div><div><h2>Параметры и воспроизводимость</h2><p>Конфигурация, пороги, нормализация, контрольные суммы и время расчёта.</p>{overview?.metadata.input_fingerprint && <span className="fingerprint">Расчёт {overview.metadata.input_fingerprint.slice(0, 16)}</span>}</div><a className="button secondary" href="/api/exports/run_metadata.json" download><Icon name="download" size={16}/>Метаданные JSON</a></section><div className="notice"><Icon name="info"/><p>Идентификаторы клиентов сохранены точно. При открытии CSV в табличном редакторе выбирайте текстовый тип для колонок gid, src и dst — длинные числа могут округляться редактором.</p></div></div>;
}
