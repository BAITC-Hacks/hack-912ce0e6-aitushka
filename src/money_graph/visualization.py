"""An offline, directed PyVis view with exact (string) client identifiers."""

from __future__ import annotations

import colorsys
import html
import json
import math
from functools import lru_cache
from pathlib import Path

import networkx as nx
import pandas as pd
import pyvis
from pyvis.network import Network


ROLE_LABELS = {
    "consolidator": "Консолидатор",
    "transit": "Транзит",
    "distributor": "Распределитель",
    "terminal": "Конечный получатель",
    "coordinator": "Координатор",
    "peripheral": "Периферия",
}
ROLE_COLORS = {
    "consolidator": "#83b9e8",
    "transit": "#87c8c9",
    "distributor": "#d7b786",
    "terminal": "#b3a4d7",
    "coordinator": "#cf99a7",
    "peripheral": "#bec7cf",
}


def cluster_color(cluster_id: int) -> str:
    """Stable colors even when a filter hides some communities."""
    hue = (int(cluster_id) * 0.618033988749895) % 1
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.40, 0.82)
    return f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"


def graph_node_ids(graph: nx.DiGraph, selected_gid: str | None, hops: int = 1) -> set[str]:
    """Get the complete incoming + outgoing ego neighborhood, including isolates."""
    lookup = {str(node): node for node in graph.nodes}
    if selected_gid is None or str(selected_gid) not in lookup:
        return set()
    visited = {lookup[str(selected_gid)]}
    frontier = visited.copy()
    for _ in range(max(0, int(hops))):
        adjacent = set()
        for node in frontier:
            adjacent.update(graph.predecessors(node))
            adjacent.update(graph.successors(node))
        frontier = adjacent - visited
        visited.update(adjacent)
    return {str(node) for node in visited}


@lru_cache(maxsize=1)
def _local_assets() -> tuple[str, str]:
    """Read the vendored browser library; no CDN/network requests are required."""
    library = Path(pyvis.__file__).resolve().parent / "templates" / "lib" / "vis-9.1.2"
    script = (library / "vis-network.min.js").read_text(encoding="utf-8")
    style = (library / "vis-network.css").read_text(encoding="utf-8")
    return script, style


def _script_json(value: object) -> str:
    # A gid or tooltip must never be able to terminate the embedding script.
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")


def render_graph_html(
    graph: nx.DiGraph,
    nodes: pd.DataFrame,
    selected_gid: str | None = None,
    color_by: str = "role",
    node_ids: set[str] | None = None,
    max_nodes: int | None = 180,
    height: int = 640,
    event_edges: dict | None = None,
    event_label: str | None = None,
) -> str:
    """Render a self-contained HTML document with all JS and CSS embedded.

    ``node_ids`` is the desired visible set. If capped, prioritize the selected
    client then the highest ranked nodes and expose exact counts in the HTML.
    PyVis handles graph serialization; its CDN-bearing default template is not
    used. All IDs sent to JavaScript are strings, preserving int64 precision.
    """
    records = nodes.to_dict("records")
    available = {str(node) for node in graph.nodes}
    wanted = available if node_ids is None else {str(gid) for gid in node_ids} & available
    if selected_gid is not None and str(selected_gid) in available:
        wanted.add(str(selected_gid))
    records = [row for row in records if str(row["gid"]) in wanted]
    records.sort(key=lambda row: (str(row["gid"]) != str(selected_gid), -float(row.get("priority_score", 0)), int(row["gid"])))
    if max_nodes is not None:
        records = records[:max(1, int(max_nodes))]
    visible = {str(row["gid"]) for row in records}
    hidden = len(wanted - visible)
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#f6f8fa", font_color="#26313d", cdn_resources="in_line")
    node_details = {}
    for row in records:
        gid = str(row["gid"])
        role = str(row.get("role", "peripheral"))
        color = cluster_color(int(row["cluster_id"])) if color_by == "cluster" else ROLE_COLORS.get(role, "#bec7cf")
        seed = bool(row.get("is_seed", False))
        boundary = bool(row.get("truncated_by_depth", False))
        selected = gid == str(selected_gid)
        note = str(row.get("observation_note", ""))
        node_details[gid] = {
            "role": ROLE_LABELS.get(role, role),
            "priority": f"{float(row.get('priority_score', 0)):.3f}",
            "incoming": f"{float(row.get('in_kzt', 0)):,.2f}".replace(",", " "),
            "outgoing": f"{float(row.get('out_kzt', 0)):,.2f}".replace(",", " "),
            "context": f"Сообщество {int(row['cluster_id'])}" + (" · seed" if seed else "") + (" · граница" if boundary else ""),
            "color": color,
        }
        tooltip = (
            f"<b>Клиент {html.escape(gid)}</b><br>"
            f"{html.escape(ROLE_LABELS.get(role, role))} · сообщество {int(row['cluster_id'])}<br>"
            f"Приоритет: {float(row.get('priority_score', 0)):.3f}<br>"
            f"Вход за период: {float(row.get('in_kzt', 0)):,.2f} KZT<br>"
            f"Выход за период: {float(row.get('out_kzt', 0)):,.2f} KZT<br>"
            f"Плательщиков: {int(row.get('in_deg', 0))} · получателей: {int(row.get('out_deg', 0))}<br>"
            f"{'Исходный seed · ' if seed else ''}{'Граница выгрузки · ' if boundary else ''}"
            f"{html.escape(note)}"
        )
        net.add_node(
            gid,
            label=gid if selected or len(visible) <= 45 else "",
            title=tooltip,
            color={"background": color, "border": "#1b232c" if seed or selected else "#ffffff", "highlight": {"background": color, "border": "#11171d"}, "hover": {"background": color, "border": "#536879"}},
            shape="diamond" if boundary else "dot",
            size=13 + 18 * float(row.get("priority_score", 0)) + (5 if selected else 0),
            borderWidth=3.5 if selected else (2.5 if seed else 1.5),
            shadow={"enabled": selected, "color": "rgba(107,155,195,0.24)", "size": 18, "x": 0, "y": 4},
            font={"size": 13, "face": "Arial", "color": "#28333f", "strokeWidth": 5, "strokeColor": "#f6f8fa"},
        )
        # PyVis replaces an empty label with the node id in its constructor.
        # Override the final payload so dense views retain only the selection.
        net.nodes[-1]["label"] = gid if selected or len(visible) <= 45 else ""
    for source, target, attrs in graph.edges(data=True):
        if str(source) not in visible or str(target) not in visible:
            continue
        amount = float(attrs.get("sum_kzt", attrs.get("weight", 0)))
        transactions = int(attrs.get("n_tx", 0))
        pair = (str(source), str(target))
        if event_edges is not None:
            if pair not in event_edges:
                continue
            amount = float(event_edges[pair]["sum_kzt"])
            transactions = int(event_edges[pair]["n_tx"])
        scope = "Операции выбранного события" if event_edges is not None else "Все операции за период"
        net.add_edge(
            str(source), str(target),
            title=f"{html.escape(str(source))} → {html.escape(str(target))}<br>{scope}<br>{amount:,.2f} KZT · {transactions} операций",
            width=4 if event_edges is not None else min(5.0, 0.7 + math.log10(1 + max(0, amount)) / 2),
            color={"color": "#527bb9" if event_edges is not None else "#8498ac", "highlight": "#253b52", "hover": "#536f8b", "opacity": 0.88},
            arrows={"to": {"enabled": True, "scaleFactor": 1.15}},
        )
    options = {
        "layout": {"randomSeed": 42, "improvedLayout": len(visible) <= 200},
        "interaction": {"hover": True, "tooltipDelay": 250, "hideEdgesOnDrag": len(visible) > 300, "keyboard": {"enabled": True, "bindToWindow": False}, "zoomSpeed": 0.7},
        "edges": {"smooth": {"enabled": True, "type": "dynamic"} if len(visible) <= 180 else False},
        "physics": {"enabled": True, "solver": "barnesHut", "barnesHut": {"gravitationalConstant": -6500, "springLength": 155, "springConstant": 0.03, "damping": 0.3}, "stabilization": {"enabled": True, "iterations": 180, "updateInterval": 25}},
    }
    javascript, styles = _local_assets()
    stats = {"requested_nodes": len(wanted), "visible_nodes": len(visible), "hidden_nodes": hidden, "visible_edges": len(net.edges)}
    event_caption = html.escape(event_label or "")
    scope_caption = f"<br>{event_caption}<br>Рёбра: только операции события" if event_edges is not None else ""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>{styles}</style><style>
* {{box-sizing:border-box;}}
html,body {{margin:0;background:#f6f8fa;font-family:Arial,sans-serif;color:#26313d;}}
body {{border:1px solid #fff;border-radius:24px;overflow:hidden;position:relative;}}
#network {{height:{height}px;width:100%;outline:none;background-image:radial-gradient(#d6dee580 .7px,transparent .7px);background-size:22px 22px;}}
.toolbar {{position:absolute;top:16px;left:16px;z-index:3;display:flex;flex-wrap:wrap;max-width:calc(100% - 32px);gap:7px;align-items:center;}}
button {{border:1px solid #ffffff;border-radius:999px;padding:10px 15px;background:#ffffffed;color:#26313d;cursor:pointer;font:500 12px Arial,sans-serif;box-shadow:0 3px 12px #152a4010;transition:background .15s,box-shadow .15s;}}
button:hover {{background:#e8f2fc;box-shadow:0 4px 15px #152a401a;}}
button:focus-visible {{outline:2px solid #86b8e4;outline-offset:3px;}}
button:disabled {{opacity:.45;cursor:default;}}
button.zoom {{width:36px;height:36px;padding:0;font-size:20px;}}
button.primary {{background:#17212a;border-color:#17212a;color:#fff;}}
button.primary:hover {{background:#314352;}}
button.motion {{color:#657381;}}
.footer {{position:absolute;bottom:16px;left:16px;right:16px;display:flex;justify-content:space-between;align-items:flex-end;gap:12px;pointer-events:none;}}
#state {{font-size:11px;background:#ffffffee;padding:10px 13px;border:1px solid #fff;border-radius:14px;line-height:1.6;color:#657381;box-shadow:0 3px 14px #23395008;}}
#state strong {{font-size:12px;font-weight:600;color:#28333f;}}
.hint {{font-size:11px;line-height:1.65;color:#788592;text-align:right;max-width:210px;}}
.inspector {{position:absolute;right:16px;bottom:84px;z-index:3;width:270px;max-width:calc(100% - 32px);padding:18px;background:#fffffff5;border:1px solid #fff;border-radius:20px;box-shadow:0 10px 40px #253b5212;}}
.inspector[hidden] {{display:none;}}
.inspector .eyebrow {{color:#7f8c97;font-size:10px;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:7px;}}
.inspector .close {{position:absolute;top:10px;right:10px;box-shadow:none;padding:4px 8px;font-size:18px;background:transparent;color:#8996a1;}}
#detail-gid {{font-size:14px;font-weight:600;letter-spacing:.15px;overflow-wrap:anywhere;margin-right:12px;}}
#detail-context {{font-size:11px;color:#86929d;line-height:1.6;margin:4px 0 12px;}}
.role {{display:inline-flex;align-items:center;gap:6px;font-size:11px;padding:5px 8px;border-radius:8px;background:#f1f6fb;margin-bottom:12px;}}
#detail-dot {{width:7px;height:7px;border-radius:50%;}}
.detail-row {{display:flex;justify-content:space-between;gap:12px;font-size:11px;line-height:1.8;color:#86929d;}}
.detail-row strong {{font-weight:500;color:#2d3d4a;text-align:right;}}
.detail-priority {{border-top:1px solid #edf1f4;margin-top:7px;padding-top:7px;}}
.open-client {{width:100%;margin-top:14px;}}
.open-client[hidden] {{display:none;}}
div.vis-tooltip {{border:1px solid #fff;border-radius:14px;background:#fffffff5;color:#293946;font:12px/1.65 Arial,sans-serif;padding:12px 15px;box-shadow:0 8px 24px #253b521c;max-width:350px;white-space:normal;}}
@media(max-width:550px) {{.hint {{display:none;}} .toolbar {{gap:5px;}} button {{padding:9px 11px;font-size:11px;}} .inspector {{width:245px;bottom:82px;}}}}
</style></head><body>
<div class="toolbar"><button class="zoom" onclick="zoom(1.3)" aria-label="Увеличить граф">+</button><button class="zoom" onclick="zoom(1/1.3)" aria-label="Уменьшить граф">−</button><button onclick="network.fit({{animation:true}})" aria-label="Показать все узлы">Вписать граф</button><button class="primary" id="focus" onclick="focusSelected()" {'disabled' if selected_gid is None else ''}>К выбранному</button><button class="motion" id="motion" onclick="toggleMotion()" aria-pressed="true">Остановить движение</button></div>
<div id="network" tabindex="0" role="img" aria-label="Направленный граф переводов"></div>
<section class="inspector" id="inspector" aria-live="polite" aria-label="Выбранный узел графа" hidden>
<button class="close" onclick="document.getElementById('inspector').hidden=true" aria-label="Закрыть информацию об узле">×</button>
<div class="eyebrow">Клиент сети</div><div id="detail-gid"></div><div id="detail-context"></div>
<div class="role"><span id="detail-dot"></span><span id="detail-role"></span></div>
<div class="detail-row"><span>Вход за период · KZT</span><strong id="detail-incoming"></strong></div>
<div class="detail-row"><span>Выход за период · KZT</span><strong id="detail-outgoing"></strong></div>
<div class="detail-row detail-priority"><span>Приоритет проверки</span><strong id="detail-priority"></strong></div>
<button class="primary open-client" id="open-client" onclick="openClient()" hidden>Открыть карточку</button>
</section>
<div class="footer"><div id="state"><strong>{len(visible)} узлов · {len(net.edges)} связей</strong><br>Скрыто по лимиту: {hidden}{scope_caption}</div><div class="hint">Колесо — масштаб · потяните — перемещение<br>Нажмите на узел, чтобы увидеть детали</div></div>
<script>{javascript}</script><script>
window.moneyGraphStats = {_script_json(stats)};
const graphData = {{nodes:new vis.DataSet({_script_json(net.nodes)}),edges:new vis.DataSet({_script_json(net.edges)})}};
const network = new vis.Network(document.getElementById('network'), graphData, {_script_json(options)});
const selectedNode = {_script_json(str(selected_gid) if selected_gid is not None else None)};
const nodeDetails = {_script_json(node_details)};
let inspectedNode = selectedNode;
let moving = true;
document.getElementById('open-client').hidden=window.parent===window;
function openClient() {{if(inspectedNode && window.parent!==window) window.parent.postMessage({{type:'money-graph:open-client',gid:inspectedNode}},window.location.origin);}}
function zoom(factor) {{ network.moveTo({{scale:Math.max(.03,Math.min(4,network.getScale()*factor)),animation:{{duration:200}}}}); }}
function inspectNode(gid) {{
    const detail=nodeDetails[gid]; if(!detail) return;
    inspectedNode=gid;
    document.getElementById('detail-gid').textContent=gid;
    ['context','role','incoming','outgoing','priority'].forEach(function(key) {{document.getElementById('detail-'+key).textContent=detail[key];}});
    document.getElementById('detail-dot').style.background=detail.color;
    document.getElementById('inspector').hidden=false;
    document.getElementById('focus').disabled=false;
}}
function focusSelected() {{ if(inspectedNode && graphData.nodes.get(inspectedNode)) {{network.selectNodes([inspectedNode]);network.focus(inspectedNode,{{scale:1.05,animation:{{duration:400}}}});inspectNode(inspectedNode);}} }}
function toggleMotion() {{ moving=!moving;network.setOptions({{physics:{{enabled:moving}}}});const button=document.getElementById('motion');button.textContent=moving?'Остановить движение':'Включить движение';button.setAttribute('aria-pressed',String(moving)); }}
network.once('stabilizationIterationsDone',function() {{if(moving) toggleMotion();}});
network.on('click',function(event) {{if(event.nodes.length) {{network.selectNodes(event.nodes);inspectNode(event.nodes[0]);}}}});
</script></body></html>"""
