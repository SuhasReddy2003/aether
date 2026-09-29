"""Provenance graph over a run's events.

Nodes: user, goal, agent, tool_call (one per Action), data_artifact
(anything an action reads or writes that can be referenced again — an
email, a file path, a DB row key), memory_entry, decision.
Edges: `read` (tool_call -> data_artifact it consumed), `wrote` (tool_call
-> data_artifact it produced), `derived_from` (a value inside one tool_call
came from a specific data_artifact), `caused` (agent/goal -> tool_call).

Built on NetworkX (a real dependency, not vendored). Queries are graph
traversals, not guesses: `why(action_id)` returns the actual predecessor
subgraph; `root_cause(action_id)` walks `caused`/`derived_from` edges
backward to the sources with no further incoming edges of those types;
`descendants(node_id)` is a forward reachability query.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import networkx as nx

NodeType = Literal["user", "goal", "agent", "tool_call", "data_artifact", "memory_entry", "decision"]
EdgeType = Literal["read", "wrote", "derived_from", "caused"]


@dataclass
class ProvenanceNode:
    node_id: str
    node_type: NodeType
    attributes: dict[str, Any] = field(default_factory=dict)


class ProvenanceGraph:
    def __init__(self) -> None:
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()

    # -- construction ----------------------------------------------------

    def add_node(self, node_id: str, node_type: NodeType, **attributes: Any) -> None:
        self.graph.add_node(node_id, node_type=node_type, **attributes)

    def add_edge(self, src: str, dst: str, edge_type: EdgeType, **attributes: Any) -> None:
        if src not in self.graph:
            raise KeyError(f"source node '{src}' not in graph; add_node it first")
        if dst not in self.graph:
            raise KeyError(f"destination node '{dst}' not in graph; add_node it first")
        self.graph.add_edge(src, dst, key=edge_type, edge_type=edge_type, **attributes)

    def has_node(self, node_id: str) -> bool:
        return node_id in self.graph

    def node_type(self, node_id: str) -> NodeType | None:
        return self.graph.nodes[node_id].get("node_type") if node_id in self.graph else None

    # -- queries -----------------------------------------------------------

    def why(self, node_id: str) -> ProvenanceGraph:
        """Return the subgraph of everything that (transitively) led to
        `node_id`: all ancestors plus the edges connecting them."""
        if node_id not in self.graph:
            raise KeyError(f"unknown node '{node_id}'")
        ancestors = nx.ancestors(self.graph, node_id) | {node_id}
        sub = ProvenanceGraph()
        sub.graph = self.graph.subgraph(ancestors).copy()
        return sub

    def root_cause(self, node_id: str) -> list[str]:
        """Walk backward along `caused`/`derived_from`/`read` edges to find
        the ultimate source node(s) — ancestors with no further incoming
        edges of those types. Multiple roots are possible (e.g. an action
        derived from two upstream artifacts)."""
        if node_id not in self.graph:
            raise KeyError(f"unknown node '{node_id}'")
        ancestors = nx.ancestors(self.graph, node_id) | {node_id}
        roots = []
        for n in ancestors:
            in_edges = self.graph.in_edges(n, keys=True)
            has_relevant_incoming = any(k in ("caused", "derived_from", "read") for _, _, k in in_edges)
            if not has_relevant_incoming and n != node_id:
                roots.append(n)
        return sorted(roots) if roots else [node_id]

    def descendants(self, node_id: str) -> set[str]:
        if node_id not in self.graph:
            raise KeyError(f"unknown node '{node_id}'")
        return nx.descendants(self.graph, node_id)

    def explain_path(self, from_node: str, to_node: str) -> list[str] | None:
        """A single readable causal chain from `from_node` to `to_node`, if
        one exists (shortest path by edge count). Returns None if no path
        exists."""
        try:
            return nx.shortest_path(self.graph, from_node, to_node)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def to_dict(self) -> dict[str, Any]:
        nodes = [{"id": n, **self.graph.nodes[n]} for n in self.graph.nodes]
        edges = [
            {"source": u, "target": v, "edge_type": data.get("edge_type"), **{k: val for k, val in data.items() if k != "edge_type"}}
            for u, v, data in self.graph.edges(data=True)
        ]
        return {"nodes": nodes, "edges": edges}
