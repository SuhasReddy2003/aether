"""Compensation registry: one compensating action per declared compensation
name. A compensator receives the original recorded Event and a `context`
dict (whatever live handles it needs — a ShadowFilesystem, a WorldState,
etc.) and returns a JSON-safe result describing what it did. Any exception
it raises is recorded, never swallowed (see RollbackEngine).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from aether.core.event import Event


class Compensator(Protocol):
    def __call__(self, original_event: Event, context: dict[str, Any]) -> dict[str, Any]: ...


class CompensationRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, Callable[[Event, dict[str, Any]], dict[str, Any]]] = {}

    def register(self, compensation_name: str, fn: Callable[[Event, dict[str, Any]], dict[str, Any]]) -> None:
        self._registry[compensation_name] = fn

    def get(self, compensation_name: str) -> Callable[[Event, dict[str, Any]], dict[str, Any]] | None:
        return self._registry.get(compensation_name)

    def has(self, compensation_name: str) -> bool:
        return compensation_name in self._registry
