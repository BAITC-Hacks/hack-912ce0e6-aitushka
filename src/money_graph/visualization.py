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
    "consolidator": "#176b68",
    "transit": "#3977b6",
    "distributor": "#ce7c27",
    "terminal": "#905cad",
    "coordinator": "#d05b69",
    "peripheral": "#9ca9b5",
}


def cluster_color(cluster_id: int) -> str:
    """Stable colors even when a filter hides some communities."""
    hue = (int(cluster_id) * 0.618033988749895) % 1
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.58, 0.72)
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
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#f7f9fc", font_color="#21334b", cdn_resources="in_line")
    for row in records:
        gid = str(row["gid"])
        role = str(row.get("role", "peripheral"))
        color = cluster_color(int(row["cluster_id"])) if color_by == "cluster" else ROLE_COLORS.get(role, "#9ca9b5")
        seed = bool(row.get("is_seed", False))
        boundary = bool(row.get("truncated_by_depth", False))
        selected = gid == str(selected_gid)
        note = str(row.get("observation_note", ""))
        tooltip = (
            f"<b>Клиент {html.escape(gid)}</b><br>"
            f"{html.escape(ROLE_LABELS.get(role, role))} · сообщество {int(row['cluster_id'])}<br>"
            f"Приоритет: {float(row.get('priority_score', 0)):.3f}<br>"
            f"Вход: {float(row.get('in_kzt', 0)):,.2f} KZT<br>"
            f"Выход: {float(row.get('out_kzt', 0)):,.2f} KZT<br>"
            f"Плательщиков: {int(row.get('in_deg', 0))} · получателей: {int(row.get('out_deg', 0))}<br>"
            f"{'Исходный seed · ' if seed else ''}{'Граница выгрузки · ' if boundary else ''}"
            f"{html.escape(note)}"
        )
        net.add_node(
            gid,
            label=gid if selected or len(visible) <= 45 else "",
            title=tooltip,
            color={"background": color, "border": "#14283f" if seed or selected else "#ffffff", "highlight": {"background": color, "border": "#14283f"}},
            shape="diamond" if boundary else "dot",
            size=13 + 18 * float(row.get("priority_score", 0)) + (5 if selected else 0),
            borderWidth=4 if selected else (3 if seed else 1),
            font={"size": 14, "face": "Arial", "strokeWidth": 4, "strokeColor": "#f7f9fc"},
        )
        # PyVis replaces an empty label with the node id in its constructor.
        # Override the final payload so dense views retain only the selection.
        net.nodes[-1]["label"] = gid if selected or len(visible) <= 45 else ""
    for source, target, attrs in graph.edges(data=True):
        if str(source) not in visible or str(target) not in visible:
            continue
        amount = float(attrs.get("sum_kzt", attrs.get("weight", 0)))
        transactions = int(attrs.get("n_tx", 0))
        net.add_edge(
            str(source), str(target),
            title=f"{html.escape(str(source))} → {html.escape(str(target))}<br>{amount:,.2f} KZT · {transactions} операций",
            width=min(5.0, 0.7 + math.log10(1 + max(0, amount)) / 2),
            color={"color": "#718ba4", "highlight": "#176b68", "hover": "#176b68", "opacity": 0.85},
            arrows={"to": {"enabled": True, "scaleFactor": 1.15}},
        )
    options = {
        "layout": {"randomSeed": 42, "improvedLayout": len(visible) <= 200},
        "interaction": {"hover": True, "tooltipDelay": 150, "hideEdgesOnDrag": len(visible) > 300, "keyboard": {"enabled": True, "bindToWindow": False}},
        "edges": {"smooth": {"enabled": True, "type": "dynamic"} if len(visible) <= 180 else False},
        "physics": {"enabled": True, "solver": "barnesHut", "barnesHut": {"gravitationalConstant": -6500, "springLength": 155, "springConstant": 0.03, "damping": 0.3}, "stabilization": {"enabled": True, "iterations": 180, "updateInterval": 25}},
    }
    javascript, styles = _local_assets()
    stats = {"requested_nodes": len(wanted), "visible_nodes": len(visible), "hidden_nodes": hidden, "visible_edges": len(net.edges)}
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>{styles}</style><style>
html,body {{margin:0;background:#f7f9fc;font-family:Arial,sans-serif;color:#41536b;}}
#network {{height:{height}px;width:100%;outline:none;}}
.toolbar {{position:absolute;top:12px;left:12px;z-index:3;display:flex;flex-wrap:wrap;max-width:calc(100% - 24px);gap:6px;align-items:center;}}
button {{border:1px solid #dae3eb;border-radius:7px;padding:8px 12px;background:white;color:#21334b;cursor:pointer;box-shadow:0 2px 6px #163b6510;}}
button:hover {{background:#edf4f7;}}
#state {{position:absolute;bottom:12px;left:12px;font-size:12px;background:#fffffff0;padding:8px 12px;border-radius:6px;pointer-events:none;}}
</style></head><body>
<div class="toolbar"><button onclick="zoom(1.3)" aria-label="Увеличить граф">+</button><button onclick="zoom(1/1.3)" aria-label="Уменьшить граф">−</button><button onclick="network.fit({{animation:true}})" aria-label="Показать все узлы">Вписать граф</button><button onclick="focusSelected()" {'disabled' if selected_gid is None else ''}>К выбранному</button><button id="motion" onclick="toggleMotion()">Остановить движение</button></div>
<div id="network" tabindex="0" role="img" aria-label="Направленный граф переводов"></div>
<div id="state">{len(visible)} узлов · {len(net.edges)} связей · скрыто по лимиту: {hidden}</div>
<script>{javascript}</script><script>
window.moneyGraphStats = {_script_json(stats)};
const graphData = {{nodes:new vis.DataSet({_script_json(net.nodes)}),edges:new vis.DataSet({_script_json(net.edges)})}};
const network = new vis.Network(document.getElementById('network'), graphData, {_script_json(options)});
const selectedNode = {_script_json(str(selected_gid) if selected_gid is not None else None)};
let moving = true;
function zoom(factor) {{ network.moveTo({{scale:Math.max(.03,Math.min(4,network.getScale()*factor)),animation:{{duration:200}}}}); }}
function focusSelected() {{ if(selectedNode && graphData.nodes.get(selectedNode)) {{network.selectNodes([selectedNode]);network.focus(selectedNode,{{scale:1.05,animation:{{duration:400}}}});}} }}
function toggleMotion() {{ moving=!moving;network.setOptions({{physics:{{enabled:moving}}}}); document.getElementById('motion').textContent=moving?'Остановить движение':'Включить движение'; }}
network.once('stabilizationIterationsDone',function() {{if(moving) toggleMotion();}});
network.on('click',function(event) {{if(event.nodes.length) network.selectNodes(event.nodes);}});
</script></body></html>"""
