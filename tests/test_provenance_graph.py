from __future__ import annotations

import pytest

from aether.provenance.graph import ProvenanceGraph


@pytest.fixture()
def graph() -> ProvenanceGraph:
    g = ProvenanceGraph()
    g.add_node("goal", "goal")
    g.add_node("agent:a1", "agent")
    g.add_node("tool_call:1", "tool_call", tool="email.read")
    g.add_node("artifact:1", "data_artifact")
    g.add_node("tool_call:2", "tool_call", tool="payment.create")
    g.add_node("decision:2", "decision")

    g.add_edge("goal", "agent:a1", "caused")
    g.add_edge("agent:a1", "tool_call:1", "caused")
    g.add_edge("tool_call:1", "artifact:1", "wrote")
    g.add_edge("agent:a1", "tool_call:2", "caused")
    g.add_edge("artifact:1", "tool_call:2", "derived_from")
    g.add_edge("tool_call:2", "decision:2", "caused")
    return g


def test_add_edge_requires_existing_nodes() -> None:
    g = ProvenanceGraph()
    g.add_node("a", "agent")
    with pytest.raises(KeyError):
        g.add_edge("a", "does_not_exist", "caused")


def test_why_returns_full_ancestor_subgraph(graph: ProvenanceGraph) -> None:
    sub = graph.why("tool_call:2")
    assert "tool_call:2" in sub.graph.nodes
    assert "artifact:1" in sub.graph.nodes
    assert "tool_call:1" in sub.graph.nodes
    assert "agent:a1" in sub.graph.nodes
    assert "goal" in sub.graph.nodes
    assert "decision:2" not in sub.graph.nodes  # descendant, not ancestor


def test_why_unknown_node_raises(graph: ProvenanceGraph) -> None:
    with pytest.raises(KeyError):
        graph.why("does_not_exist")


def test_root_cause_finds_the_goal(graph: ProvenanceGraph) -> None:
    roots = graph.root_cause("tool_call:2")
    assert "goal" in roots


def test_descendants_of_artifact_includes_the_sink(graph: ProvenanceGraph) -> None:
    descendants = graph.descendants("artifact:1")
    assert "tool_call:2" in descendants
    assert "decision:2" in descendants
    assert "tool_call:1" not in descendants  # sibling, not descendant


def test_explain_path_gives_readable_chain(graph: ProvenanceGraph) -> None:
    path = graph.explain_path("tool_call:1", "tool_call:2")
    assert path == ["tool_call:1", "artifact:1", "tool_call:2"]


def test_explain_path_returns_none_when_no_path(graph: ProvenanceGraph) -> None:
    assert graph.explain_path("tool_call:2", "tool_call:1") is None


def test_to_dict_serializes_nodes_and_edges(graph: ProvenanceGraph) -> None:
    d = graph.to_dict()
    assert len(d["nodes"]) == 6
    assert any(e["edge_type"] == "derived_from" for e in d["edges"])
