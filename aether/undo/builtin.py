"""Built-in compensators shipped with Aether.

These are generic enough to reuse across examples/integrations: they only
assume the shape of data our own sandbox primitives (`ShadowFilesystem`,
`WorldState`) already produce, not anything demo-specific.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aether.core.event import Event
from aether.sandbox.filesystem import ShadowFilesystem
from aether.state.world import WorldState


def fs_restore_write(event: Event, context: dict[str, Any]) -> dict[str, Any]:
    """Undo a `fs.write` (or `fs.create` that reused this compensation): if
    the file didn't exist before, delete it; otherwise restore its prior
    content exactly."""
    fs: ShadowFilesystem = context["fs"]
    path = event.action.arguments["virtual_path"]
    previous_content = (event.action.result or {}).get("previous_content")
    if previous_content is None:
        fs.delete(path)
        return {"action": "deleted", "path": path}
    fs.write(path, previous_content)
    return {"action": "restored", "path": path}


def fs_undo_create(event: Event, context: dict[str, Any]) -> dict[str, Any]:
    """Undo an `fs.create`: the file did not exist before, so delete it."""
    fs: ShadowFilesystem = context["fs"]
    path = event.action.arguments["virtual_path"]
    fs.delete(path)
    return {"action": "deleted", "path": path}


def fs_undo_delete(event: Event, context: dict[str, Any]) -> dict[str, Any]:
    """Undo an `fs.delete`: recreate the file with its exact prior content."""
    fs: ShadowFilesystem = context["fs"]
    path = event.action.arguments["virtual_path"]
    previous_content = (event.action.result or {}).get("previous_content")
    if previous_content is None:
        return {"action": "noop", "note": "deleted path had no prior file content to restore"}
    fs.create(path, previous_content)
    return {"action": "recreated", "path": path}


def world_restore_from_result(key_arg_name: str, result_previous_field: str = "previous") -> Callable[[Event, dict[str, Any]], dict[str, Any]]:
    """Factory for a compensator over any `WorldState`-backed tool that (a)
    takes the id it mutated as an argument named `key_arg_name`, and (b)
    returns the pre-mutation value of that id under `result[result_previous_field]`.

    This covers any "update a record identified by id, in a JSON-file-backed
    world" tool (a CRM note, a ticket status, ...) without hardcoding any
    particular domain concept.
    """

    def _compensator(event: Event, context: dict[str, Any]) -> dict[str, Any]:
        world: WorldState = context["world"]
        key = event.action.arguments[key_arg_name]
        previous = (event.action.result or {}).get(result_previous_field)
        data = world.load()
        if previous is None:
            data.pop(key, None)
        else:
            data[key] = previous
        world.save(data)
        return {"key": key, "restored_to": previous}

    return _compensator


def register_builtin_compensators(registry: Any) -> None:
    registry.register("fs.restore_write", fs_restore_write)
    registry.register("fs.undo_create", fs_undo_create)
    registry.register("fs.undo_delete", fs_undo_delete)
