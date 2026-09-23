from html.parser import HTMLParser
import json
import re

from money_graph.features import build_graph
from money_graph.pipeline import run_analysis
from money_graph.visualization import graph_node_ids, render_graph_html


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and "src" in attributes:
            self.references.append(attributes["src"])
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.references.append(attributes.get("href", ""))


def graph_payload(document):
    # Validate the actual browser-bound values rather than merely matching gid text in a tooltip.
    marker = re.search(r"const graphData\s*=\s*\{nodes:new vis\.DataSet\(", document)
    assert marker, "The browser graph must contain its client dataset"
    decoder = json.JSONDecoder()
    nodes, consumed = decoder.raw_decode(document[marker.end():])
    remainder = document[marker.end() + consumed:]
    edge_marker = re.search(r"edges:new vis\.DataSet\(", remainder)
    assert edge_marker
    edges, _ = decoder.raw_decode(remainder[edge_marker.end():])
    return nodes, edges


def test_graph_keeps_exact_string_ids_and_direction_without_external_resources(data_dir, config):
    result = run_analysis(data_dir, config)
    document = render_graph_html(result.graph, result.nodes, max_nodes=None)
    nodes, edges = graph_payload(document)
    assert {node["id"] for node in nodes} == {str(gid) for gid in result.nodes["gid"]}
    assert all(isinstance(node["id"], str) for node in nodes)
    expected_edges = {(str(source), str(target)) for source, target in result.graph.edges}
    assert {(edge["from"], edge["to"]) for edge in edges} == expected_edges
    assert all(edge["arrows"]["to"]["enabled"] for edge in edges)
    parser = ResourceParser()
    parser.feed(document)
    assert parser.references == []


def test_graph_cap_preserves_selected_low_priority_client_and_reports_hidden_nodes(data_dir, config):
    result = run_analysis(data_dir, config)
    isolated = result.nodes.loc[result.nodes["in_deg"].eq(0) & result.nodes["out_deg"].eq(0)].iloc[-1]
    selected = str(isolated["gid"])
    document = render_graph_html(result.graph, result.nodes, selected_gid=selected, max_nodes=2)
    nodes, edges = graph_payload(document)
    assert len(nodes) == 2
    assert selected in {node["id"] for node in nodes}
    marker = re.search(r"window\.moneyGraphStats\s*=\s*", document)
    assert marker
    stats, _ = json.JSONDecoder().raw_decode(document[marker.end():])
    assert stats["requested_nodes"] == 26
    assert stats["visible_nodes"] == 2
    assert stats["hidden_nodes"] == 24
    visible = {node["id"] for node in nodes}
    assert all(edge["from"] in visible and edge["to"] in visible for edge in edges)


def test_neighborhood_contains_incoming_outgoing_and_isolated_clients(valid_frames):
    edges, nodes, _ = valid_frames
    graph = build_graph(edges, nodes)
    gids = nodes["gid"].map(str).tolist()
    assert graph_node_ids(graph, gids[1], hops=1) == {gids[0], gids[1], gids[2], gids[5]}
    assert gids[3] in graph_node_ids(graph, gids[1], hops=2)
    assert graph_node_ids(graph, gids[-1], hops=2) == {gids[-1]}
