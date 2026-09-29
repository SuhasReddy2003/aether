"""MCP proxy: a transparent recording proxy in front of a REAL MCP server
(any stdio-based server; tested against the official, open-source
`@modelcontextprotocol/server-filesystem` package — no mock server is
substituted, see tests/test_mcp_proxy.py).

Every tool call is recorded through the normal Aether recorder/hash chain,
with an honest side-effect and reversibility classification. Where genuine
reversal is possible (overwriting an existing file, moving a file), a real
compensator is registered that reconnects to the same server and undoes the
change for real. Where it is not possible — most notably, this particular
server has NO delete/rmdir tool, so a brand-new file or directory cannot be
undone through it — that is declared honestly rather than faked.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aether.core.action import Action, ActionStatus, Authorization
from aether.core.side_effects import SideEffect, SideEffectType
from aether.runtime.interceptor import Aether, RunHandle


class AetherMCPProxyError(Exception):
    pass


@dataclass
class ToolClassification:
    side_effects: list[SideEffect]
    extra_result: dict[str, Any] | None = None


Classifier = Callable[["AetherMCPProxy", str, dict[str, Any]], Awaitable[ToolClassification]]

READ_ONLY_TOOLS = {
    "read_file", "read_text_file", "read_media_file", "read_multiple_files",
    "list_directory", "list_directory_with_sizes", "directory_tree",
    "search_files", "get_file_info", "list_allowed_directories",
}


def _extract_text(call_tool_result: Any) -> str | None:
    try:
        for block in call_tool_result.content:
            if getattr(block, "type", None) == "text":
                return block.text
    except (AttributeError, TypeError):
        pass
    return None


def _result_to_jsonsafe(call_tool_result: Any) -> Any:
    try:
        return {
            "is_error": call_tool_result.is_error,
            "text": _extract_text(call_tool_result),
            "structured_content": call_tool_result.structured_content,
        }
    except AttributeError:
        return {"raw": str(call_tool_result)}


async def _classify_read_only(_proxy: AetherMCPProxy, _tool: str, _arguments: dict[str, Any]) -> ToolClassification:
    return ToolClassification(side_effects=[SideEffect(type=SideEffectType.READ)])


async def _classify_write_file(proxy: AetherMCPProxy, _tool: str, arguments: dict[str, Any]) -> ToolClassification:
    path = arguments.get("path")
    previous_content: str | None = None
    existed = False
    if path and proxy.session is not None:
        try:
            existing = await proxy.session.call_tool("read_text_file", {"path": path})
            if not getattr(existing, "is_error", False):
                previous_content = _extract_text(existing)
                existed = True
        except Exception:  # noqa: BLE001 - genuinely means "couldn't read, treat as new"
            existed = False
    if existed:
        return ToolClassification(
            side_effects=[SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation="mcp.fs.restore_write")],
            extra_result={"previous_content": previous_content},
        )
    return ToolClassification(
        side_effects=[SideEffect(type=SideEffectType.CREATE, reversible=False)],
        extra_result={
            "previous_content": None,
            "reversibility_note": "server has no delete_file tool; a newly created file cannot be undone through this proxy",
        },
    )


async def _classify_move_file(_proxy: AetherMCPProxy, _tool: str, arguments: dict[str, Any]) -> ToolClassification:
    return ToolClassification(
        side_effects=[SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation="mcp.fs.restore_move")],
        extra_result={"source": arguments.get("source"), "destination": arguments.get("destination")},
    )


async def _classify_create_directory(_proxy: AetherMCPProxy, _tool: str, _arguments: dict[str, Any]) -> ToolClassification:
    return ToolClassification(
        side_effects=[SideEffect(type=SideEffectType.CREATE, reversible=False)],
        extra_result={"reversibility_note": "server has no rmdir tool; directory creation cannot be undone through this proxy"},
    )


async def _classify_edit_file(_proxy: AetherMCPProxy, _tool: str, _arguments: dict[str, Any]) -> ToolClassification:
    # edit_file applies a diff-style patch; safely reversing it would require
    # capturing and replaying the inverse patch, which is not implemented in
    # Phase 2. Declared honestly as not reversible rather than guessed at.
    return ToolClassification(side_effects=[SideEffect(type=SideEffectType.UPDATE, reversible=False)])


DEFAULT_CLASSIFIERS: dict[str, Classifier] = {
    "write_file": _classify_write_file,
    "move_file": _classify_move_file,
    "create_directory": _classify_create_directory,
    "edit_file": _classify_edit_file,
}


class AetherMCPProxy:
    """Usage:

        params = StdioServerParameters(command="node", args=[server_js, root_dir])
        async with AetherMCPProxy(aether, params, agent="fs_agent") as proxy:
            await proxy.call_tool("write_file", {"path": ..., "content": ...})
    """

    def __init__(
        self,
        aether: Aether,
        server_params: StdioServerParameters,
        agent: str,
        goal: str = "",
        run_id: str | None = None,
        classifiers: dict[str, Classifier] | None = None,
    ) -> None:
        self.aether = aether
        self.server_params = server_params
        self.agent = agent
        self.goal = goal
        self.run_id = run_id
        self.classifiers = {**DEFAULT_CLASSIFIERS, **(classifiers or {})}
        self._run_handle: RunHandle | None = None
        self._stdio_ctx: Any = None
        self._session_ctx: Any = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        self._stdio_ctx = stdio_client(self.server_params)
        read, write = await self._stdio_ctx.__aenter__()
        self._session_ctx = ClientSession(read, write)
        self.session = await self._session_ctx.__aenter__()
        await self.session.initialize()
        self._run_handle = self.aether.run(agent=self.agent, goal=self.goal, run_id=self.run_id)
        await self._run_handle.__aenter__()
        self.run_id = self._run_handle.run_id
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._run_handle:
            await self._run_handle.__aexit__(*exc)
        if self._session_ctx:
            await self._session_ctx.__aexit__(*exc)
        if self._stdio_ctx:
            await self._stdio_ctx.__aexit__(*exc)

    async def list_tools(self) -> list[Any]:
        assert self.session is not None
        result = await self.session.list_tools()
        return list(result.tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        assert self.session is not None
        ctx = self.aether._require_context()
        classification = await self._classify(tool, arguments)

        start = time.perf_counter()
        error: str | None = None
        success = True
        result: Any = None
        try:
            result = await self.session.call_tool(tool, arguments)
            if getattr(result, "is_error", False):
                success = False
                error = _extract_text(result) or "MCP tool reported is_error=True"
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            error = f"{type(exc).__name__}: {exc}"
            success = False
        duration_ms = (time.perf_counter() - start) * 1000.0

        recorded_result: dict[str, Any] = _result_to_jsonsafe(result) if result is not None else {}
        if classification.extra_result:
            recorded_result.update(classification.extra_result)

        action = Action(
            run_id=ctx.run_id,
            step=ctx.next_step(),
            agent_id=ctx.agent_id,
            agent_version=ctx.agent_version,
            tool=f"mcp.{tool}",
            arguments=arguments,
            result=recorded_result,
            error=error,
            status=ActionStatus.SUCCESS if success else ActionStatus.ERROR,
            authorization=Authorization(role=ctx.role, agent_id=ctx.agent_id),
            side_effects=classification.side_effects,
            duration_ms=duration_ms,
            parent_action_id=ctx.last_event_id,
        )
        event = self.aether.recorder.record(action, parent_event_id=ctx.last_event_id)
        ctx.last_event_id = event.event_id

        if not success:
            raise AetherMCPProxyError(error or f"MCP tool '{tool}' failed")
        return result

    async def _classify(self, tool: str, arguments: dict[str, Any]) -> ToolClassification:
        if tool in READ_ONLY_TOOLS:
            return await _classify_read_only(self, tool, arguments)
        classifier = self.classifiers.get(tool)
        if classifier:
            return await classifier(self, tool, arguments)
        return ToolClassification(side_effects=[SideEffect(type=SideEffectType.EXTERNAL_CALL, reversible=False)])


# -- real compensators that reconnect to the same MCP server -----------------

def _run_async(coro: Any) -> Any:
    """Run `coro` to completion regardless of whether we're already inside a
    running event loop (e.g. rollback invoked from async code) or not (the
    ordinary CLI/script case). `asyncio.run()` alone raises RuntimeError in
    the former case — found by the real MCP proxy test suite, not assumed."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()


def mcp_fs_restore_write(server_params: StdioServerParameters) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
    def _compensator(event: Any, _context: dict[str, Any]) -> dict[str, Any]:
        path = event.action.arguments.get("path")
        previous_content = (event.action.result or {}).get("previous_content")
        if previous_content is None:
            return {
                "action": "noop",
                "note": "file did not exist before this write; no delete tool on this MCP "
                "server to undo the creation (documented limitation)",
            }

        async def _restore() -> None:
            async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("write_file", {"path": path, "content": previous_content})

        _run_async(_restore())
        return {"action": "restored", "path": path}

    return _compensator


def mcp_fs_restore_move(server_params: StdioServerParameters) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
    def _compensator(event: Any, _context: dict[str, Any]) -> dict[str, Any]:
        source = event.action.arguments.get("source")
        destination = event.action.arguments.get("destination")

        async def _restore() -> None:
            async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("move_file", {"source": destination, "destination": source})

        _run_async(_restore())
        return {"action": "moved_back", "from": destination, "to": source}

    return _compensator


def register_mcp_compensators(registry: Any, server_params: StdioServerParameters) -> None:
    registry.register("mcp.fs.restore_write", mcp_fs_restore_write(server_params))
    registry.register("mcp.fs.restore_move", mcp_fs_restore_move(server_params))


def locate_filesystem_server_script() -> Path | None:
    """Best-effort discovery of a local install of the official, open-source
    `@modelcontextprotocol/server-filesystem` npm package, so tests and the
    demo don't depend on `npx`'s network/cache behavior (which was found to
    be unreliable in sandboxed environments during development — direct
    `node <script>.js` invocation of a locally-installed copy was not).

    Checks, in order:
      1. `AETHER_MCP_FS_SERVER` env var (explicit path to index.js)
      2. `<repo_root>/.mcp_servers/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js`
         (created by `npm install --prefix .mcp_servers @modelcontextprotocol/server-filesystem`)
      3. the global npm root

    Returns None (never raises) if not found, so callers can skip cleanly.
    """
    import os
    import subprocess

    env_path = os.environ.get("AETHER_MCP_FS_SERVER")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    repo_root = Path(__file__).resolve().parents[2]
    local_candidate = repo_root / ".mcp_servers" / "node_modules" / "@modelcontextprotocol" / "server-filesystem" / "dist" / "index.js"
    if local_candidate.exists():
        return local_candidate

    try:
        global_root = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
        if global_root:
            global_candidate = Path(global_root) / "@modelcontextprotocol" / "server-filesystem" / "dist" / "index.js"
            if global_candidate.exists():
                return global_candidate
    except (OSError, subprocess.SubprocessError):
        pass

    return None
