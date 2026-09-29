"""MCP proxy demo (Phase 2): connects to the REAL, official, open-source
`@modelcontextprotocol/server-filesystem` package (confined to a temp
directory), records a session through it, and rolls back the reversible
part of that session for real.

Requires Node.js and a local install of the server package:
    npm install --prefix .mcp_servers @modelcontextprotocol/server-filesystem
(or set AETHER_MCP_FS_SERVER to the server's index.js path).
If neither is available, this script prints what's missing and exits
cleanly rather than faking a result.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether.integrations.mcp_proxy import locate_filesystem_server_script

DATA_DIR = Path.home() / ".aether"


async def main() -> None:
    server_script = locate_filesystem_server_script()
    if server_script is None or shutil.which("node") is None:
        print(
            "SKIPPED: real @modelcontextprotocol/server-filesystem not found.\n"
            "Install it with:\n"
            "  npm install --prefix .mcp_servers @modelcontextprotocol/server-filesystem\n"
            "or set AETHER_MCP_FS_SERVER to its index.js path."
        )
        return

    from mcp import StdioServerParameters

    from aether import Aether
    from aether.integrations.mcp_proxy import AetherMCPProxy, register_mcp_compensators
    from aether.undo.compensation import CompensationRegistry
    from aether.undo.rollback import RollbackEngine

    fs_root = Path(tempfile.mkdtemp(prefix="aether_mcp_demo_"))
    print(f"Real filesystem MCP server confined to: {fs_root}")

    notice_path = fs_root / "refund_notice.txt"
    notice_path.write_text("Refund of $84.00 approved for cust_1001.")

    server_params = StdioServerParameters(command="node", args=[str(server_script), str(fs_root)])
    aether = Aether(data_dir=DATA_DIR)

    async with AetherMCPProxy(aether, server_params, agent="fs_agent", goal="Update refund notice") as proxy:
        run_id = proxy.run_id
        tools = await proxy.list_tools()
        print(f"Connected. Real server exposes {len(tools)} tools: {[t.name for t in tools][:5]}...")

        await proxy.call_tool("write_file", {"path": str(notice_path), "content": "Refund of $2,840.00 approved. URGENT."})
        await proxy.call_tool("read_text_file", {"path": str(notice_path)})
        print(f"After (suspicious) overwrite, real file on disk reads: {notice_path.read_text()!r}")

    print(f"\nRun recorded: {run_id}")
    print(f"Inspect with: aether inspect {run_id}")
    print(f"Verify with:  aether verify {run_id}")

    registry = CompensationRegistry()
    register_mcp_compensators(registry, server_params)
    engine = RollbackEngine(aether.recorder, registry)
    report = engine.rollback_to(run_id, target_step=0)

    print("\nRolling back via a fresh reconnect to the SAME real MCP server...")
    for r in report.results:
        print(f"  step {r.step} {r.tool:<20} -> {r.outcome:<12} {r.detail[:70]}")

    restored = notice_path.read_text()
    print(f"\nAfter rollback, real file on disk reads: {restored!r}")
    assert restored == "Refund of $84.00 approved for cust_1001.", "rollback did not restore the real file!"
    print("VERIFIED: the real file on real disk was restored via the real MCP server, exactly.")

    shutil.rmtree(fs_root, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
