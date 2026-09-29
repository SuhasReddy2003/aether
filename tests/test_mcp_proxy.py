"""Tests for the MCP proxy — run against the REAL, official, open-source
`@modelcontextprotocol/server-filesystem` package over real stdio, not a
mock. If Node.js or the package isn't available in this environment, these
tests skip cleanly (never silently pass, never fake success) with a message
saying exactly what's missing and how to install it.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aether.integrations.mcp_proxy import locate_filesystem_server_script

pytest.importorskip("mcp", reason="the 'mcp' package (aether[mcp] extra) is not installed")

from mcp import StdioServerParameters

from aether.integrations.mcp_proxy import (
    AetherMCPProxy,
    AetherMCPProxyError,
    register_mcp_compensators,
)
from aether.recording.hashchain import verify_chain
from aether.runtime.interceptor import Aether
from aether.undo.compensation import CompensationRegistry
from aether.undo.rollback import RollbackEngine

SERVER_SCRIPT = locate_filesystem_server_script()
NODE_AVAILABLE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(
    SERVER_SCRIPT is None or not NODE_AVAILABLE,
    reason=(
        "Real @modelcontextprotocol/server-filesystem not found. Install with: "
        "npm install --prefix .mcp_servers @modelcontextprotocol/server-filesystem "
        "(or set AETHER_MCP_FS_SERVER to the server's index.js path)."
    ),
)


@pytest.fixture()
def fs_root(tmp_path: Path) -> Path:
    root = tmp_path / "mcp_fs_root"
    root.mkdir()
    return root


@pytest.fixture()
def server_params(fs_root: Path) -> StdioServerParameters:
    return StdioServerParameters(command="node", args=[str(SERVER_SCRIPT), str(fs_root)])


@pytest.mark.anyio
async def test_list_tools_returns_real_server_tools(server_params, aether_instance: Aether) -> None:
    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        tools = await proxy.list_tools()
        names = {t.name for t in tools}
    assert "write_file" in names
    assert "read_text_file" in names


@pytest.mark.anyio
async def test_write_and_read_actually_hits_real_disk(server_params, aether_instance: Aether, fs_root: Path) -> None:
    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        await proxy.call_tool("write_file", {"path": str(fs_root / "hello.txt"), "content": "hello from aether"})
        result = await proxy.call_tool("read_text_file", {"path": str(fs_root / "hello.txt")})

    # it really landed on disk, not just recorded
    assert (fs_root / "hello.txt").read_text() == "hello from aether"
    assert "hello from aether" in str(result)


@pytest.mark.anyio
async def test_calls_are_recorded_with_valid_hash_chain(server_params, aether_instance: Aether, fs_root: Path) -> None:
    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        run_id = proxy.run_id
        await proxy.call_tool("write_file", {"path": str(fs_root / "a.txt"), "content": "v1"})
        await proxy.call_tool("read_text_file", {"path": str(fs_root / "a.txt")})

    events = aether_instance.recorder.storage.get_events(run_id)
    assert len(events) == 2
    assert events[0].action.tool == "mcp.write_file"
    assert events[1].action.tool == "mcp.read_text_file"
    valid, bad_id = verify_chain(events)
    assert valid, f"chain invalid at {bad_id}"


@pytest.mark.anyio
async def test_new_file_is_classified_create_and_not_reversible(server_params, aether_instance: Aether, fs_root: Path) -> None:
    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        run_id = proxy.run_id
        await proxy.call_tool("write_file", {"path": str(fs_root / "new.txt"), "content": "brand new"})

    events = aether_instance.recorder.storage.get_events(run_id)
    effect = events[0].action.side_effects[0]
    assert effect.type.value == "create"
    assert effect.reversible is False  # honest: no delete tool on this server


@pytest.mark.anyio
async def test_overwrite_of_existing_file_is_reversible_and_rollback_restores_it(
    server_params, aether_instance: Aether, fs_root: Path
) -> None:
    target = fs_root / "notice.txt"
    target.write_text("ORIGINAL CONTENT")

    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        run_id = proxy.run_id
        await proxy.call_tool("write_file", {"path": str(target), "content": "OVERWRITTEN CONTENT"})

    assert target.read_text() == "OVERWRITTEN CONTENT"

    events = aether_instance.recorder.storage.get_events(run_id)
    effect = events[0].action.side_effects[0]
    assert effect.reversible is True
    assert effect.compensation == "mcp.fs.restore_write"

    registry = CompensationRegistry()
    register_mcp_compensators(registry, server_params)
    engine = RollbackEngine(aether_instance.recorder, registry)
    report = engine.rollback_to(run_id, target_step=0)

    assert len(report.compensated) == 1
    # real disk content restored via a REAL reconnect to the REAL server
    assert target.read_text() == "ORIGINAL CONTENT"


@pytest.mark.anyio
async def test_move_file_round_trip_via_rollback(server_params, aether_instance: Aether, fs_root: Path) -> None:
    source = fs_root / "src.txt"
    dest = fs_root / "dst.txt"
    source.write_text("payload")

    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        run_id = proxy.run_id
        await proxy.call_tool("move_file", {"source": str(source), "destination": str(dest)})

    assert not source.exists()
    assert dest.read_text() == "payload"

    registry = CompensationRegistry()
    register_mcp_compensators(registry, server_params)
    engine = RollbackEngine(aether_instance.recorder, registry)
    report = engine.rollback_to(run_id, target_step=0)

    assert len(report.compensated) == 1
    assert source.read_text() == "payload"
    assert not dest.exists()


@pytest.mark.anyio
async def test_failed_call_is_recorded_as_error_and_raises(server_params, aether_instance: Aether, fs_root: Path) -> None:
    async with AetherMCPProxy(aether_instance, server_params, agent="fs_agent") as proxy:
        run_id = proxy.run_id
        with pytest.raises(AetherMCPProxyError):
            await proxy.call_tool("read_text_file", {"path": str(fs_root / "does_not_exist.txt")})

    events = aether_instance.recorder.storage.get_events(run_id)
    assert events[0].action.status.value == "error"


@pytest.mark.anyio
async def test_bypass_the_proxy_calling_server_directly(server_params, fs_root: Path) -> None:
    """Self-audit style test (see docs/self-audit-phase*.md): confirm and
    DOCUMENT what happens if an agent bypasses the Aether proxy and talks to
    the underlying MCP server directly. Answer: nothing stops it — Aether
    can only see and record what goes through the proxy. This is a stated
    assumption in the threat model, not a gap this test hides."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        await session.call_tool("write_file", {"path": str(fs_root / "bypassed.txt"), "content": "never recorded"})

    # the write succeeded on real disk with NO Aether record of it at all —
    # this is the documented, honest limitation, not a bug to "fix" here.
    assert (fs_root / "bypassed.txt").read_text() == "never recorded"
