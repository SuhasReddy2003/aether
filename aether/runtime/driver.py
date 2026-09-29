"""Agent driver: an interface for running an "agent" against a scenario.

Without this abstraction, replay/fork/regression testing have nothing
uniform to re-run. `ScriptedAgent` is a deterministic, no-LLM driver: a
fixed list of (tool_name, kwargs) steps executed against a tool registry.
It is what every Phase 3 demo and test uses. An `OllamaAgent` (optional,
non-deterministic, clearly labeled) can implement the same `AgentDriver`
protocol later without changing anything that consumes it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ScenarioStep:
    tool: str
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """A fixed, ordered list of tool calls an agent will make. This is the
    unit that gets replayed, forked, and diffed."""

    name: str
    steps: list[ScenarioStep]
    goal: str = ""


class AgentDriver(Protocol):
    """Anything that can execute a Scenario against a tool registry and
    return the run_id it recorded under."""

    def run_scenario(self, scenario: Scenario, tools: dict[str, Callable[..., Any]], run_id: str | None = None) -> str: ...


class ScriptedAgent:
    """Deterministic driver: executes each `ScenarioStep` in order against
    a dict of already-`@aether.tool`-decorated callables. No model calls,
    no randomness — the same scenario always produces the same sequence of
    tool invocations (though tool results may still depend on external
    mutable state, e.g. a shadow filesystem, unless that's reset first)."""

    def __init__(self, aether: Any, agent_id: str = "scripted_agent", agent_version: str = "v1") -> None:
        self.aether = aether
        self.agent_id = agent_id
        self.agent_version = agent_version

    def run_scenario(self, scenario: Scenario, tools: dict[str, Callable[..., Any]], run_id: str | None = None) -> str:
        with self.aether.run(
            agent=self.agent_id, goal=scenario.goal, agent_version=self.agent_version, run_id=run_id
        ) as handle:
            for step in scenario.steps:
                if step.tool not in tools:
                    raise KeyError(f"scenario '{scenario.name}' references undeclared tool '{step.tool}'")
                tools[step.tool](**step.kwargs)
        return handle.run_id
