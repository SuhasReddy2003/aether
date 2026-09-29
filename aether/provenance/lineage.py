"""Builds a ProvenanceGraph from a run's recorded events, wiring in taint
findings so the graph directly answers "why did it do this?" for a tainted
sink action: `Email (untrusted) -> agent context -> payment.amount ->
payment API` becomes a real, queryable path, not just a printed string.
"""
from __future__ import annotations

from aether.core.event import Event
from aether.provenance.graph import ProvenanceGraph
from aether.security.taint import TaintFinding, TaintTracker


def build_provenance_graph(events: list[Event], agent_id: str, goal: str = "") -> tuple[ProvenanceGraph, list[TaintFinding]]:
    """Construct the graph and return (graph, taint_findings) so callers get
    both the structural provenance and the taint evidence in one pass."""
    graph = ProvenanceGraph()
    graph.add_node("goal", "goal", text=goal)
    graph.add_node(f"agent:{agent_id}", "agent", agent_id=agent_id)
    graph.add_edge("goal", f"agent:{agent_id}", "caused")

    tracker = TaintTracker()
    findings: list[TaintFinding] = []
    artifact_node_for_event: dict[str, str] = {}

    for event in events:
        action = event.action
        tool_call_id = f"tool_call:{action.action_id}"
        graph.add_node(
            tool_call_id, "tool_call",
            tool=action.tool, step=action.step, status=action.status.value,
            event_id=event.event_id,
        )
        graph.add_edge(f"agent:{agent_id}", tool_call_id, "caused")

        if action.untrusted_output and action.status.value == "success":
            artifact_id = f"artifact:{event.event_id}"
            graph.add_node(artifact_id, "data_artifact", source_tool=action.tool, event_id=event.event_id)
            graph.add_edge(tool_call_id, artifact_id, "wrote")
            artifact_node_for_event[event.event_id] = artifact_id

        finding = tracker.check_sink(event)
        if finding is not None:
            findings.append(finding)
            if finding.is_tainted:
                decision_id = f"decision:{action.action_id}"
                graph.add_node(decision_id, "decision", tainted_context=finding.tainted_context,
                                tainted_value=finding.tainted_value, confidence=finding.confidence)
                graph.add_edge(tool_call_id, decision_id, "caused")
                for span in finding.matched_spans:
                    source_artifact = artifact_node_for_event.get(span.source_event_id)
                    if source_artifact and graph.has_node(source_artifact):
                        graph.add_edge(
                            source_artifact, tool_call_id, "derived_from",
                            argument_key=span.argument_key, matched_text=span.matched_text,
                            confidence=span.confidence,
                        )

        tracker.ingest(event)

    return graph, findings


def explain_action(graph: ProvenanceGraph, action_id: str) -> str:
    """Plain-language causal chain for a tool_call node, e.g.:
    'email.read -> derived_from -> payment.create'."""
    tool_call_id = f"tool_call:{action_id}"
    if not graph.has_node(tool_call_id):
        return f"No provenance recorded for action '{action_id}'."

    roots = graph.root_cause(tool_call_id)
    lines = []
    for root in roots:
        path = graph.explain_path(root, tool_call_id)
        if path:
            readable = " -> ".join(_describe_node(graph, n) for n in path)
            lines.append(readable)
    return "\n".join(lines) if lines else f"No causal path found to '{action_id}'."


def _describe_node(graph: ProvenanceGraph, node_id: str) -> str:
    node_type = graph.node_type(node_id)
    attrs = graph.graph.nodes[node_id]
    if node_type == "tool_call":
        return str(attrs.get("tool", node_id))
    if node_type == "data_artifact":
        return f"{attrs.get('source_tool', 'artifact')} output"
    if node_type == "agent":
        return f"agent:{attrs.get('agent_id', node_id)}"
    if node_type == "decision":
        return "decision"
    return node_id
