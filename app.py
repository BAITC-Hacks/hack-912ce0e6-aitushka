"""Local analyst workspace. Start with ``python run.py --ui``."""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import streamlit as st

from money_graph.config import load_config
from money_graph.export import export_result
from money_graph.pipeline import input_fingerprint, run_analysis
from money_graph.visualization import ROLE_COLORS, ROLE_LABELS, cluster_color, graph_node_ids, render_graph_html


st.set_page_config(page_title="Граф денег · Aitushka", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
.block-container {padding-top:2rem;padding-bottom:2rem;max-width:1580px;}
h1 {letter-spacing:-1.5px;font-weight:750!important;}
h2,h3 {letter-spacing:-.45px;}
[data-testid="stMetric"] {background:white;border:1px solid #e1e8ef;border-radius:12px;padding:15px 18px;}
[data-testid="stMetricLabel"] {color:#60738a;font-size:13px;}
[data-testid="stMetricValue"] {font-size:27px;font-weight:650;}
[data-testid="stSidebar"] {border-right:1px solid #e1e8ef;}
.eyebrow {font-size:11px;font-weight:750;letter-spacing:2px;color:#176b68;margin-bottom:8px;}
.subtitle {color:#65758b;max-width:850px;line-height:1.6;margin-top:-6px;margin-bottom:24px;}
.brand {font-size:22px;font-weight:800;letter-spacing:-.6px;color:#20334b;margin:2px 0 2px;}
.brand-sub {color:#697b90;font-size:12px;margin-bottom:26px;}
.legend {display:flex;gap:10px 16px;flex-wrap:wrap;font-size:12px;color:#52657c;margin:0 0 12px;}
.dot {display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;}
.node-role {display:inline-block;border-radius:6px;padding:4px 9px;background:#e5f0ed;color:#176b68;font-size:13px;font-weight:650;}
div[data-testid="stTabs"] button[role="tab"] {font-size:15px;}
</style>""", unsafe_allow_html=True)


def number(value: float | int, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ")


def money(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{number(value / 1_000_000, 2)} млн"
    return number(value)


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def display_frame(frame: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    """Keep IDs as text before a browser sees them, including Arrow tables."""
    selected = frame[[col for col in columns if col in frame]].copy()
    for col in ("gid", "src", "dst"):
        if col in selected:
            selected[col] = selected[col].map(str)
    for col in ("role", "alternative_role"):
        if col in selected:
            selected[col] = selected[col].map(lambda value: ROLE_LABELS.get(value, value))
    return selected.rename(columns=columns)


@st.cache_data(show_spinner=False, max_entries=4)
def analyze_cached(data_dir: str, config: dict, fingerprint: str):
    # fingerprint includes all file contents, parameters, and analytical logic.
    return run_analysis(Path(data_dir), config)


with st.sidebar:
    st.markdown('<div class="brand">◈ Граф денег</div><div class="brand-sub">AITUSHKA · ANALYST WORKSPACE</div>', unsafe_allow_html=True)
    st.caption("ИССЛЕДОВАНИЕ СЕТИ")
    with st.expander("Параметры запуска"):
        data_path = st.text_input("Папка данных", os.environ.get("MONEY_GRAPH_DATA", str(ROOT / "data")))
        config_path = st.text_input("Конфигурация", os.environ.get("MONEY_GRAPH_CONFIG", str(ROOT / "config.json")))
        output_path = st.text_input("Папка результатов", os.environ.get("MONEY_GRAPH_OUT", str(ROOT / "outputs")))
    if st.button("Пересчитать анализ", width="stretch", type="primary"):
        analyze_cached.clear()

try:
    config = load_config(Path(config_path))
    fingerprint = input_fingerprint(Path(data_path), config)
    with st.spinner("Проверяем данные и восстанавливаем структуру переводов…"):
        result = analyze_cached(data_path, config, fingerprint)
except Exception as exc:
    st.title("Граф денег")
    st.error(f"Не удалось выполнить анализ: {exc}")
    st.info("Проверьте папку данных и конфигурацию слева. Нужны nodes.parquet, edges.parquet и transactions.parquet.")
    st.stop()

nodes = result.nodes.copy()
nodes["gid_text"] = nodes["gid"].map(str)
node_lookup = {str(row["gid"]): row for row in nodes.to_dict("records")}
transactions = result.transactions
period_start = pd.to_datetime(transactions["date"]).min()
period_end = pd.to_datetime(transactions["date"]).max()
period_label = f"{period_start:%d.%m.%Y} — {period_end:%d.%m.%Y}" if pd.notna(period_start) else "Период без операций"

with st.sidebar:
    st.divider()
    st.caption("ФИЛЬТРЫ ОБЗОРА И СООБЩЕСТВ")
    role_options = [role for role in ROLE_LABELS if role in set(nodes["role"])]
    chosen_roles = st.multiselect("Роли клиентов", role_options, format_func=lambda role: ROLE_LABELS[role], placeholder="Все роли")
    cluster_options = sorted(int(value) for value in nodes["cluster_id"].unique())
    chosen_clusters = st.multiselect("Сообщества", cluster_options, placeholder="Все сообщества", format_func=lambda value: f"Сообщество {value}")
    seed_filter = st.selectbox("Связь с исходной выборкой", ["Все клиенты", "Только seed", "Без seed"])
    boundary_filter = st.selectbox("Наблюдаемость", ["Все уровни", "Граница выгрузки", "Внутри границы"])

filtered = nodes
if chosen_roles:
    filtered = filtered[filtered["role"].isin(chosen_roles)]
if chosen_clusters:
    filtered = filtered[filtered["cluster_id"].isin(chosen_clusters)]
if seed_filter != "Все клиенты":
    filtered = filtered[filtered["is_seed"] == (seed_filter == "Только seed")]
if boundary_filter != "Все уровни":
    filtered = filtered[filtered["truncated_by_depth"] == (boundary_filter == "Граница выгрузки")]
filtered = filtered.sort_values(["priority_score", "gid"], ascending=[False, True])

with st.sidebar:
    st.caption(f"В фильтре: {number(len(filtered))} из {number(len(nodes))} клиентов")
    st.divider()
    st.caption("НАБЛЮДАЕМАЯ ВЫБОРКА")
    st.markdown(f"**{period_label}**")
    st.caption("Обход от seed по исходящим переводам. Граница сети ограничивает выводы о дальнейших переводах.")
    st.caption(f"Расчёт: {fingerprint[:12]}")

st.markdown('<div class="eyebrow">ФИНАНСОВАЯ СЕТЬ / ПРИОРИТЕТЫ ПРОВЕРКИ</div>', unsafe_allow_html=True)
st.title("От переводов — к структуре")
st.markdown('<div class="subtitle">Находите точки схождения, изучайте цепочки и проверяйте гипотезы о ролях клиентов. Каждая оценка связана с наблюдаемыми числами.</div>', unsafe_allow_html=True)

metrics = st.columns(4)
metrics[0].metric("Клиентов в сети", number(len(nodes)))
metrics[1].metric("Направленных связей", number(len(result.edges)))
metrics[2].metric("Наблюдаемый оборот · KZT", money(float(transactions["sum_kzt"].sum())))
metrics[3].metric("Сообществ", number(len(result.clusters)))
runtime = result.metadata.get("timings", {}).get("total_seconds")
runtime_label = f" · расчёт {float(runtime):.2f} с" if runtime is not None else ""
st.caption(f"{period_label} · {number(len(transactions))} операций · {int(nodes['is_seed'].sum())} исходных клиентов (seed){runtime_label}")

if st.session_state.get("selected_gid") not in node_lookup:
    st.session_state.selected_gid = str(result.top_nodes["gid"].iloc[0]) if len(result.top_nodes) else nodes["gid_text"].iloc[0]

with st.form("global_gid_search", border=False):
    search_columns = st.columns([5, 1])
    query = search_columns[0].text_input("Поиск клиента по gid", placeholder="Полный gid — поиск во всей сети, независимо от фильтров", label_visibility="collapsed")
    submitted = search_columns[1].form_submit_button("Найти клиента", width="stretch")
if submitted:
    clean_query = query.strip()
    if clean_query in node_lookup:
        st.session_state.selected_gid = clean_query
        st.success(f"Клиент {clean_query} выбран. Его окружение — во вкладке «Граф», детали — в «Карточке».")
    elif not clean_query:
        st.info("Введите полный идентификатор клиента.")
    else:
        st.warning(f"Клиент {clean_query} отсутствует в предоставленном наборе. Проверьте полный gid.")

selected_gid = st.session_state.selected_gid
selected = node_lookup[selected_gid]
overview_tab, graph_tab, card_tab = st.tabs(["Обзор сети", "Граф переводов", "Карточка клиента"])

with overview_tab:
    st.subheader("Кого проверить первым")
    st.caption("Приоритет отражает схождение seed-цепочек, оборот, посредничество и число плательщиков. Шкала 0–1.")
    if filtered.empty:
        st.info("Нет клиентов с такими фильтрами. Измените ограничения слева; глобальный поиск остаётся доступен.")
    else:
        top = filtered.head(20)
        st.dataframe(display_frame(top, {"gid": "Клиент · gid", "role": "Гипотеза роли", "priority_score": "Приоритет", "role_score": "Сила признаков", "cluster_id": "Сообщество", "why": "Почему в приоритете"}), hide_index=True, width="stretch",
                     column_config={"Приоритет": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.3f"), "Сила признаков": st.column_config.NumberColumn(format="%.3f"), "Почему в приоритете": st.column_config.TextColumn(width="large")})
        quick_cols = st.columns([4, 1])
        quick_gid = quick_cols[0].selectbox("Открыть клиента из топа", top["gid_text"].tolist(), format_func=lambda gid: f"{gid} · {ROLE_LABELS[node_lookup[gid]['role']]}")
        with quick_cols[1]:
            st.write("")
            st.write("")
            if st.button("Выбрать", width="stretch"):
                st.session_state.selected_gid = quick_gid
                st.rerun()

    st.divider()
    composition, communities = st.columns([1, 1.6], gap="large")
    with composition:
        st.subheader("Роли в текущем срезе")
        counts = filtered["role"].value_counts().rename_axis("Роль").reset_index(name="Клиентов")
        counts["Роль"] = counts["Роль"].map(ROLE_LABELS)
        st.bar_chart(counts, x="Роль", y="Клиентов", horizontal=True, color="#176b68", height=290)
    with communities:
        st.subheader("Структурные сообщества")
        cluster_frame = result.clusters[result.clusters["cluster_id"].isin(filtered["cluster_id"].unique())]
        st.dataframe(display_frame(cluster_frame, {"cluster_id": "Сообщество", "n_nodes": "Клиентов", "n_seed": "Seed", "sum_kzt_internal": "Внутри · KZT", "hypothesis": "Гипотеза"}), hide_index=True, width="stretch", height=290)

    boundary_count = int(nodes["truncated_by_depth"].sum())
    st.info(f"{boundary_count} клиентов находятся на границе выгрузки. Отсутствие их исходящих связей не доказывает, что деньги остановились. Роли и сообщества — гипотезы для проверки.")
    with st.expander("Скачать результаты текущего расчёта", expanded=True):
        st.caption("Выгрузки содержат всю сеть и используют тот же расчёт, что показан выше. Фильтры не обрезают файлы.")
        exports = st.columns(3)
        node_export = result.nodes.drop(columns=["gid_text"], errors="ignore")
        for col, filename, frame in zip(exports, ["nodes_roles.csv", "clusters.csv", "top_nodes.csv"], [node_export, result.clusters, result.top_nodes]):
            col.download_button(filename, data=csv_bytes(frame), file_name=filename, mime="text/csv", width="stretch")
        if st.button("Сохранить CSV и метаданные в папку результатов"):
            try:
                export_result(result, Path(output_path))
                st.success(f"Результаты сохранены: {output_path}")
            except Exception as exc:
                st.error(f"Не удалось сохранить результаты: {exc}")

with graph_tab:
    st.subheader("Связи клиента и его окружение")
    st.caption(f"Выбран клиент {selected_gid} · сообщество {selected['cluster_id']} · {ROLE_LABELS[selected['role']]}")
    controls = st.columns([2, 1, 1])
    graph_mode = controls[0].selectbox("Область графа", ["Окружение клиента", "Сообщество клиента", "Весь отфильтрованный граф"])
    hops = controls[1].selectbox("Глубина окружения", [1, 2], format_func=lambda n: f"{n} переход" if n == 1 else f"{n} перехода", disabled=graph_mode != "Окружение клиента")
    color_label = controls[2].selectbox("Цвет узлов", ["По роли", "По сообществу"])
    full = st.checkbox("Показать все узлы выбранной области (может замедлить граф)", value=False)
    if graph_mode == "Окружение клиента":
        visible_ids = graph_node_ids(result.graph, selected_gid, hops)
        st.caption("Окружение включает входящие и исходящие связи. Для выбранного клиента показаны все соседи независимо от фильтров слева.")
    elif graph_mode == "Сообщество клиента":
        visible_ids = set(filtered.loc[filtered["cluster_id"] == selected["cluster_id"], "gid_text"]) | {selected_gid}
    else:
        visible_ids = set(filtered["gid_text"]) | {selected_gid}
    limit = None if full else 180
    shown = len(visible_ids) if full else min(180, len(visible_ids))
    st.caption(f"В области {len(visible_ids)} узлов · показано {shown} · скрыто по лимиту {len(visible_ids) - shown}. Метрики клиента всегда рассчитаны на полной сети.")
    if color_label == "По роли":
        legend = "".join(f'<span><i class="dot" style="background:{color}"></i>{html.escape(ROLE_LABELS[role])}</span>' for role, color in ROLE_COLORS.items())
    else:
        cluster_ids = sorted(nodes.loc[nodes["gid_text"].isin(visible_ids), "cluster_id"].unique())
        legend = "".join(f'<span><i class="dot" style="background:{cluster_color(int(cluster))}"></i>Сообщество {cluster}</span>' for cluster in cluster_ids[:15])
        if len(cluster_ids) > 15:
            legend += f"<span>и ещё {len(cluster_ids) - 15} сообществ</span>"
    st.markdown(f'<div class="legend">{legend}</div>', unsafe_allow_html=True)
    st.caption("● Размер — приоритет · тёмная обводка — seed или выбранный клиент · ◆ граница выгрузки · стрелка — направление перевода")
    try:
        graph_html = render_graph_html(result.graph, result.nodes, selected_gid, color_by="role" if color_label == "По роли" else "cluster", node_ids=visible_ids, max_nodes=limit)
        st.iframe(graph_html, height=660)
        st.download_button("Скачать этот граф · HTML без интернета", graph_html.encode("utf-8"), file_name=f"graph_{selected_gid}.html", mime="text/html")
    except Exception as exc:
        st.error(f"Не удалось построить граф: {exc}")
    st.caption("Перетаскивайте узлы, масштабируйте колесом, наведите курсор для чисел. Достижимость по графу не доказывает движение одних и тех же денег.")

with card_tab:
    st.subheader(f"Клиент {selected_gid}")
    st.markdown(f'<span class="node-role">{html.escape(ROLE_LABELS[selected["role"]])}</span>', unsafe_allow_html=True)
    st.caption(f"Сообщество {selected['cluster_id']} · глубина {selected['depth']} · {'исходный seed' if selected['is_seed'] else 'клиент наблюдаемой цепочки'}")
    card_metrics = st.columns(4)
    card_metrics[0].metric("Приоритет проверки", f"{selected['priority_score']:.3f}")
    card_metrics[1].metric("Сила признаков роли", f"{selected['role_score']:.3f}")
    card_metrics[2].metric("Вход · KZT", money(float(selected["in_kzt"])))
    card_metrics[3].metric("Выход · KZT", money(float(selected["out_kzt"])))
    st.write(str(selected["evidence"]))
    if selected.get("observation_note"):
        st.warning(str(selected["observation_note"]))
    alternative = selected.get("alternative_role")
    if alternative and alternative in ROLE_LABELS and alternative != selected["role"]:
        st.caption(f"Альтернативная гипотеза: {ROLE_LABELS[alternative]}. Оценка роли — сила эвристических признаков, не вероятность нарушения.")
    else:
        st.caption("Оценка роли — сила эвристических признаков, не вероятность нарушения.")
    evidence_columns = st.columns([1, 1.2], gap="large")
    with evidence_columns[0]:
        st.subheader("Из чего складывается приоритет")
        contribution_names = {"contribution_seed_convergence": "Схождение seed-цепочек", "contribution_observed_flow": "Наблюдаемый оборот", "contribution_brokerage": "Посредничество", "contribution_fan_in": "Число плательщиков"}
        contributions = pd.DataFrame([{"Фактор": label, "Вклад": float(selected.get(key, 0))} for key, label in contribution_names.items()])
        st.bar_chart(contributions, x="Фактор", y="Вклад", horizontal=True, color="#176b68", height=260)
        st.caption(f"Сумма вкладов: {contributions['Вклад'].sum():.3f}. Формула и нормализация зафиксированы в конфигурации расчёта.")
    with evidence_columns[1]:
        st.subheader("Наблюдаемые признаки")
        ratio = selected.get("pass_through")
        feature_rows = [
            ("Входящий / исходящий оборот · KZT", f"{number(float(selected['in_kzt']), 2)} / {number(float(selected['out_kzt']), 2)}"),
            ("Уникальных плательщиков / получателей", f"{selected['in_deg']} / {selected['out_deg']}"),
            ("Входящих / исходящих операций", f"{selected['in_tx']} / {selected['out_tx']}"),
            ("Различных seed выше по цепочке", str(selected.get("seed_reach", 0))),
            ("Выход / вход", "не определено: нет входа" if pd.isna(ratio) else f"{float(ratio):.3f}"),
            ("PageRank", f"{float(selected.get('pagerank', 0)):.6f}"),
            ("Посредничество", f"{float(selected.get('betweenness', 0)):.6f}"),
            ("Связанных других сообществ", str(selected.get("intercluster_degree", 0))),
            ("Активных дней", str(selected.get("active_days", 0))),
        ]
        st.dataframe(pd.DataFrame(feature_rows, columns=["Признак", "Значение"]), hide_index=True, width="stretch")

    st.subheader("Потоки по дням")
    incoming = transactions[transactions["dst"].map(str) == selected_gid].copy()
    outgoing = transactions[transactions["src"].map(str) == selected_gid].copy()
    if incoming.empty and outgoing.empty:
        st.info("У клиента нет операций в предоставленной выборке. Он сохранён в результатах и доступен на графе как изолированный узел.")
    else:
        incoming["day"] = pd.to_datetime(incoming["date"]).dt.normalize()
        outgoing["day"] = pd.to_datetime(outgoing["date"]).dt.normalize()
        daily = pd.concat([incoming.groupby("day")["sum_kzt"].sum().rename("Вход · KZT"), outgoing.groupby("day")["sum_kzt"].sum().rename("Выход · KZT")], axis=1, sort=True).fillna(0)
        daily = daily.reindex(pd.date_range(period_start, period_end, freq="D"), fill_value=0)
        daily.index.name = "Дата"
        st.line_chart(daily, color=["#176b68", "#ce7c27"], height=250)
        if "temporal_matched_kzt" in selected:
            st.caption(f"Совместимый с транзитом через 1–{config.get('temporal_window_days', 2)} дня объём: {number(float(selected['temporal_matched_kzt']), 2)} KZT; доля от меньшего из входа и выхода: {float(selected.get('temporal_share', 0)):.1%}. Операции одного дня не сопоставляются: их порядок неизвестен.")

    incoming_neighbors, outgoing_neighbors = st.columns(2)
    with incoming_neighbors:
        st.subheader("Кто отправлял")
        in_edges = result.edges[result.edges["dst"].map(str) == selected_gid].sort_values("sum_kzt", ascending=False)
        st.dataframe(display_frame(in_edges, {"src": "Плательщик · gid", "sum_kzt": "Сумма · KZT", "n_tx": "Операций"}), hide_index=True, width="stretch")
    with outgoing_neighbors:
        st.subheader("Куда отправлял")
        out_edges = result.edges[result.edges["src"].map(str) == selected_gid].sort_values("sum_kzt", ascending=False)
        st.dataframe(display_frame(out_edges, {"dst": "Получатель · gid", "sum_kzt": "Сумма · KZT", "n_tx": "Операций"}), hide_index=True, width="stretch")
    with st.expander("Все операции клиента"):
        client_transactions = transactions[(transactions["src"].map(str) == selected_gid) | (transactions["dst"].map(str) == selected_gid)].sort_values("date")
        st.dataframe(display_frame(client_transactions, {"date": "Дата", "src": "Плательщик · gid", "dst": "Получатель · gid", "sum_kzt": "Сумма · KZT"}), hide_index=True, width="stretch")
        st.download_button("Скачать операции клиента", csv_bytes(client_transactions), file_name=f"transactions_{selected_gid}.csv", mime="text/csv")

st.divider()
st.caption("Граф денег · Aitushka · Все вычисления выполняются локально. Приоритеты и роли предназначены для выбора следующей проверки.")
