"""Aether CLI: run, inspect, events, verify, tools, export, import, demo, doctor."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aether.core.errors import AetherCassetteError, AetherIntegrityError, AetherRollbackError
from aether.recording.cassette import export_cassette, import_cassette
from aether.recording.hashchain import verify_chain
from aether.recording.recorder import DEFAULT_DATA_DIR, Recorder
from aether.recording.verified import load_verified_events
from aether.storage.sqlite import SQLiteStorage

app = typer.Typer(add_completion=False, help="Aether: Git for Autonomous AI Actions.")
console = Console()

STATUS_COLOR = {"success": "green", "error": "red"}


def _storage(data_dir: Path | None = None) -> SQLiteStorage:
    d = data_dir or DEFAULT_DATA_DIR
    return SQLiteStorage(d / "aether.db")


@app.command()
def run(script: str = typer.Argument(..., help="Path to a Python example script to execute.")) -> None:
    """Run an example agent script (thin wrapper around `python <script>`)."""
    result = subprocess.run([sys.executable, script], check=False)
    raise typer.Exit(result.returncode)


@app.command()
def inspect(run_id: str) -> None:
    """Show a timeline table for a run."""
    storage = _storage()
    events = storage.get_events(run_id)
    if not events:
        console.print(f"[red]No events found for run '{run_id}'[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Run {run_id}", show_lines=False)
    table.add_column("Step", justify="right")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Duration (ms)", justify="right")
    table.add_column("Hash (first 10)")
    for e in events:
        color = STATUS_COLOR.get(e.action.status.value, "white")
        table.add_row(
            str(e.action.step),
            e.action.tool,
            Text(e.action.status.value, style=color),
            f"{e.action.duration_ms:.2f}",
            e.hash[:10],
        )
    console.print(table)


@app.command()
def events(run_id: str, step: int | None = typer.Option(None, help="Show only this step.")) -> None:
    """Print full event detail (arguments, result, side effects)."""
    storage = _storage()
    all_events = storage.get_events(run_id)
    for e in all_events:
        if step is not None and e.action.step != step:
            continue
        console.print(
            Panel(
                f"[bold]tool:[/bold] {e.action.tool}\n"
                f"[bold]arguments:[/bold] {e.action.arguments}\n"
                f"[bold]result:[/bold] {e.action.result}\n"
                f"[bold]status:[/bold] {e.action.status.value}\n"
                f"[bold]side_effects:[/bold] {[s.model_dump() for s in e.action.side_effects]}\n"
                f"[bold]event_id:[/bold] {e.event_id}\n"
                f"[bold]hash:[/bold] {e.hash}",
                title=f"Step {e.action.step}",
            )
        )


@app.command()
def verify(run_id: str) -> None:
    """Verify the hash chain and signature for a run."""
    storage = _storage()
    all_events = storage.get_events(run_id)
    if not all_events:
        console.print(f"[red]No events found for run '{run_id}'[/red]")
        raise typer.Exit(1)

    valid, bad_id = verify_chain(all_events)
    if not valid:
        console.print(
            Panel(
                f"[bold red]INVALID[/bold red]\nFirst tampered event: [bold]{bad_id}[/bold]",
                title=f"aether verify {run_id}",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    sig = storage.get_run_signature(run_id)
    if not sig:
        console.print(
            Panel(
                "[bold red]INVALID: UNSIGNED[/bold red]\nThe hash chain is internally consistent, but the run has no "
                "signature. A bare chain can be rewritten and recomputed by anyone, so it is not treated as verified.",
                title=f"aether verify {run_id}",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    from aether.recording.cassette import key_fingerprint
    from aether.recording.signing import KeyPair, verify_signature

    sig_ok = verify_signature(sig["public_key_b64"], all_events[-1].hash.encode("utf-8"), sig["signature_b64"])
    if not sig_ok:
        console.print(Panel("[bold red]INVALID SIGNATURE[/bold red]", title=f"aether verify {run_id}", border_style="red"))
        raise typer.Exit(1)

    key_path = DEFAULT_DATA_DIR / "signing_key"
    local_pub = KeyPair.load_or_create(key_path).public_key_b64() if key_path.exists() else None
    is_local = local_pub == sig["public_key_b64"]
    signer_line = (
        f"Signer: {key_fingerprint(sig['public_key_b64'])} "
        + ("[green](this machine's key)[/green]" if is_local
           else "[yellow](NOT this machine's key: integrity is proven, authenticity is not, "
                "unless you independently trust this fingerprint)[/yellow]")
    )
    console.print(
        Panel(
            f"[bold green]VALID[/bold green]\n{len(all_events)} events verified\n"
            f"Head hash: {all_events[-1].hash}\n{signer_line}",
            title=f"aether verify {run_id}",
            border_style="green",
        )
    )


@app.command()
def tools() -> None:
    """List distinct tools seen across all recorded runs."""
    storage = _storage()
    conn = storage.raw_connection()
    rows = conn.execute(
        "SELECT tool, COUNT(*) as calls, SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors "
        "FROM tool_calls GROUP BY tool ORDER BY calls DESC"
    ).fetchall()
    table = Table(title="Tools")
    table.add_column("Tool")
    table.add_column("Calls", justify="right")
    table.add_column("Errors", justify="right")
    for r in rows:
        table.add_row(r["tool"], str(r["calls"]), str(r["errors"]))
    console.print(table)


@app.command(name="export")
def export_cmd(run_id: str, output: Path = typer.Option(..., "-o", "--output")) -> None:
    """Export a run to a portable .aether cassette file."""
    storage = _storage()
    try:
        path = export_cassette(storage, run_id, output)
    except AetherCassetteError as exc:
        console.print(f"[red]Export failed: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Exported[/green] {run_id} -> {path}")


@app.command(name="import")
def import_cmd(
    path: Path,
    trusted_key: list[str] = typer.Option(
        [], "--trusted-key", help="Base64 public key to trust (repeatable). If given, cassettes signed by "
        "any other key are rejected. Without it, only integrity (not authenticity) is established.",
    ),
    allow_unsigned: bool = typer.Option(False, "--allow-unsigned", help="Accept a cassette with no signature."),
) -> None:
    """Import a .aether cassette file, verifying it first."""
    storage = _storage()
    try:
        run_id = import_cassette(
            storage, path, verify=True, require_signature=not allow_unsigned,
            trusted_public_keys=set(trusted_key) if trusted_key else None,
        )
    except (AetherCassetteError, AetherIntegrityError) as exc:
        console.print(f"[red]Import rejected: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Imported[/green] run '{run_id}' from {path}")


@app.command()
def demo() -> None:
    """Run the Phase 1 showcase: scripted support agent, then verify."""
    console.print(Panel("Aether Demo — running the scripted support agent (offline, no network)", style="cyan"))
    example = Path(__file__).resolve().parents[2] / "examples" / "support_agent.py"
    result = subprocess.run([sys.executable, str(example)], check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command()
def doctor() -> None:
    """Check environment health: python version, key file, DB integrity, chain validity."""
    ok = True
    console.print(Panel("Aether Doctor", style="cyan"))

    py_ok = sys.version_info >= (3, 11)
    console.print(f"[{'green' if py_ok else 'red'}]{'✓' if py_ok else '✗'}[/] Python {sys.version.split()[0]} (>= 3.11 required)")
    ok &= py_ok

    key_path = DEFAULT_DATA_DIR / "signing_key"
    key_ok = key_path.exists()
    console.print(f"[{'green' if key_ok else 'yellow'}]{'✓' if key_ok else '!'}[/] Signing key at {key_path}" + ("" if key_ok else " (will be created on first run)"))

    db_path = DEFAULT_DATA_DIR / "aether.db"
    if db_path.exists():
        storage = SQLiteStorage(db_path)
        run_ids = storage.get_run_ids()
        console.print(f"[green]✓[/] Database at {db_path} ({len(run_ids)} run(s))")
        bad_runs = []
        for rid in run_ids:
            all_events = storage.get_events(rid)
            valid, _ = verify_chain(all_events)
            if not valid:
                bad_runs.append(rid)
        if bad_runs:
            console.print(f"[red]✗[/] {len(bad_runs)} run(s) FAIL chain verification: {bad_runs}")
            ok = False
        else:
            console.print(f"[green]✓[/] All {len(run_ids)} run(s) pass chain verification")
    else:
        console.print(f"[yellow]![/] No database yet at {db_path}")

    for optional in ("networkx", "hypothesis"):
        try:
            __import__(optional)
            console.print(f"[green]✓[/] optional dependency '{optional}' available")
        except ImportError:
            console.print(f"[yellow]![/] optional dependency '{optional}' not installed")

    if not ok:
        raise typer.Exit(1)


def _world_context(data_dir: Path, run_id: str) -> dict:
    """Reconstruct the conventional per-run WorldState + ShadowFilesystem
    context so `checkpoint`/`checkout`/`rollback` work as a genuine
    cross-process operation, not something that only works inside the demo
    script's own Python process."""
    from aether.sandbox.filesystem import ShadowFilesystem
    from aether.state.world import WorldState

    fs = ShadowFilesystem(root=data_dir / "shadow_fs" / run_id)
    world = WorldState(data_dir=data_dir, run_id=run_id)
    return {"fs": fs, "world": world}


def _combined_state(context: dict) -> dict:
    return {"fs_files": context["fs"].list_all(), "world": context["world"].load()}


def _default_registry():
    from aether.undo.builtin import register_builtin_compensators, world_restore_from_result
    from aether.undo.compensation import CompensationRegistry

    registry = CompensationRegistry()
    register_builtin_compensators(registry)
    # Registered under the same name the example agents use for any
    # WorldState-backed, id-keyed record update (e.g. `crm.update`).
    registry.register("crm.restore_previous", world_restore_from_result("customer_id"))
    return registry


@app.command()
def checkpoint(run_id: str, label: str = typer.Option("checkpoint", "--label")) -> None:
    """Save a content-hashed snapshot of the run's world state + shadow
    filesystem at the current step, so later rollback fidelity can be
    verified against it."""
    import json as _json

    from aether.state.snapshot import content_hash as _hash

    storage = _storage()
    events = storage.get_events(run_id)
    if not events:
        console.print(f"[red]No events found for run '{run_id}'[/red]")
        raise typer.Exit(1)
    current_step = events[-1].action.step

    context = _world_context(DEFAULT_DATA_DIR, run_id)
    data = _combined_state(context)
    h = _hash(data)
    snapshot_id = storage.save_snapshot(run_id, current_step, label, _json.dumps(data), h)
    console.print(f"[green]Checkpoint saved[/green] {snapshot_id} at step {current_step}, hash {h[:16]}...")


@app.command()
def checkout(run_id: str, step: int = typer.Option(..., "--step")) -> None:
    """Show the nearest saved checkpoint at or before `step` (read-only time
    travel view; use `rollback` to actually mutate live state back)."""
    storage = _storage()
    snap = storage.get_snapshot_at_or_before(run_id, step)
    if not snap:
        console.print(f"[yellow]No checkpoint at or before step {step} for run '{run_id}'[/yellow]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"[bold]step:[/bold] {snap['step']}\n[bold]label:[/bold] {snap['label']}\n"
            f"[bold]content_hash:[/bold] {snap['content_hash']}\n[bold]data:[/bold] {snap['data_json']}",
            title=f"checkout {run_id} @ step<={step}",
        )
    )


@app.command()
def rollback(run_id: str, to: int = typer.Option(..., "--to", help="Target step to roll back to.")) -> None:
    """Roll back all reversible actions after `--to` step, applying
    registered compensations. Irreversible actions are reported honestly,
    never faked as undone."""
    from aether.recording.recorder import Recorder
    from aether.undo.rollback import RollbackEngine

    recorder = Recorder(data_dir=DEFAULT_DATA_DIR)
    context = _world_context(DEFAULT_DATA_DIR, run_id)
    engine = RollbackEngine(recorder, _default_registry())

    from aether.state.snapshot import content_hash as _content_hash

    snap = _storage().get_snapshot_at_or_before(run_id, to)
    verify_kwargs: dict = {}
    if snap is not None:
        verify_kwargs = {
            "expected_state_hash": snap["content_hash"],
            "state_probe": lambda: _content_hash(_combined_state(context)),
        }

    try:
        report = engine.rollback_to(run_id, target_step=to, context=context, **verify_kwargs)
    except AetherRollbackError as exc:
        console.print(f"[red]Rollback failed: {exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title=f"Rollback {run_id} -> step {to}")
    table.add_column("Step", justify="right")
    table.add_column("Tool")
    table.add_column("Outcome")
    table.add_column("Detail")
    for r in report.results:
        color = {"compensated": "green", "irreversible": "yellow", "failed": "red", "skipped_error_status": "dim"}.get(r.outcome, "white")
        table.add_row(str(r.step), r.tool, Text(r.outcome, style=color), r.detail[:80])
    console.print(table)

    if report.state_verified is True:
        console.print(f"[green]State verified:[/green] {report.state_detail}")
    elif report.state_verified is False:
        console.print(f"[bold red]{report.state_detail}[/bold red]")
        raise typer.Exit(1)
    else:
        console.print("[yellow]State NOT verified[/yellow] (no checkpoint at/before this step; "
                      "run `aether checkpoint` before mutating to enable verification)")

    if report.failures:
        raise typer.Exit(1)


@app.command()
def watch(run_id: str, interval: float = typer.Option(0.5, "--interval")) -> None:
    """Live-tail a run's events (polls the database; useful while a run is
    still in progress in another process)."""
    import time

    storage = _storage()
    seen: set[str] = set()
    console.print(Panel(f"Watching run {run_id} (Ctrl+C to stop)", style="cyan"))
    try:
        while True:
            for e in storage.get_events(run_id):
                if e.event_id in seen:
                    continue
                seen.add(e.event_id)
                color = STATUS_COLOR.get(e.action.status.value, "white")
                console.print(
                    f"[dim]step {e.action.step}[/dim] [{color}]{e.action.tool}[/{color}] "
                    f"({e.action.status.value}, {e.action.duration_ms:.1f}ms)"
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]stopped watching[/dim]")


@app.command()
def replay(run_id: str) -> None:
    """Replay a run in strict mode: reconstruct its recorded tool
    sequence, arguments, and results directly from storage (no tool code
    is re-executed — a portable cassette carries no executable code)."""
    from aether.core.errors import AetherReplayError
    from aether.replay.player import replay_strict

    try:
        result = replay_strict(Recorder(data_dir=DEFAULT_DATA_DIR), run_id)
    except (AetherReplayError, AetherIntegrityError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title=f"Replay {run_id} (strict mode)")
    table.add_column("Step", justify="right")
    table.add_column("Tool")
    table.add_column("Arguments")
    table.add_column("Result")
    table.add_column("Status")
    for s in result.steps:
        color = STATUS_COLOR.get(s.status, "white")
        table.add_row(str(s.step), s.tool, str(s.arguments)[:40], str(s.result)[:40], Text(s.status, style=color))
    console.print(table)


@app.command(name="diff")
def diff_cmd(run_a: str, run_b: str) -> None:
    """Diff two runs: first divergence, plus tool-call/write/external-call/
    high-risk-action deltas."""
    from aether.replay.comparator import diff_runs

    recorder = Recorder(data_dir=DEFAULT_DATA_DIR)
    try:
        d = diff_runs(recorder, run_a, run_b)
    except AetherIntegrityError as exc:
        console.print(f"[red]Refusing to diff: {exc}[/red]")
        raise typer.Exit(1) from exc

    if d.first_divergence:
        fd = d.first_divergence
        console.print(
            Panel(
                f"[bold]step {fd.step}[/bold]: {fd.reason}\n"
                f"  {run_a}: {fd.run_a_tool}\n  {run_b}: {fd.run_b_tool}",
                title="First divergence",
                border_style="yellow",
            )
        )
    else:
        console.print(Panel("No divergence — runs are identical on tool/argument sequence.", border_style="green"))

    table = Table(title="Summary deltas")
    table.add_column("Metric")
    table.add_column(run_a, justify="right")
    table.add_column(run_b, justify="right")
    table.add_row("tool calls", str(d.tool_calls_a), str(d.tool_calls_b))
    table.add_row("writes", str(d.writes_a), str(d.writes_b))
    table.add_row("external/comm calls", str(d.external_calls_a), str(d.external_calls_b))
    table.add_row("high-risk actions", str(d.high_risk_a), str(d.high_risk_b))
    console.print(table)


@app.command()
def why(run_id: str, step: int = typer.Option(..., "--step")) -> None:
    """Explain why an action happened: rebuilds the provenance graph from
    stored events and reports taint evidence (never proof) for that step."""
    from aether.provenance.lineage import build_provenance_graph, explain_action

    storage = _storage()
    try:
        events = load_verified_events(storage, run_id)
    except AetherIntegrityError as exc:
        console.print(f"[red]Refusing to explain: {exc}[/red]")
        raise typer.Exit(1) from exc
    if not events:
        console.print(f"[red]No events found for run '{run_id}'[/red]")
        raise typer.Exit(1)
    target = next((e for e in events if e.action.step == step), None)
    if target is None:
        console.print(f"[red]No action at step {step} in run '{run_id}'[/red]")
        raise typer.Exit(1)

    agent_id = events[0].action.agent_id
    graph, findings = build_provenance_graph(events, agent_id=agent_id)
    finding = next((f for f in findings if f.action_id == target.action.action_id), None)

    console.print(
        Panel(
            explain_action(graph, target.action.action_id),
            title=f"why: {run_id} step {step} ({target.action.tool})",
        )
    )
    if finding is not None:
        color = "red" if finding.tainted_value else ("yellow" if finding.tainted_context else "green")
        console.print(
            f"[{color}]tainted_context={finding.tainted_context} "
            f"tainted_value={finding.tainted_value} confidence={finding.confidence}[/{color}]"
        )
        for span in finding.matched_spans:
            console.print(
                f"  MATCH: '{span.argument_key}'={span.matched_text!r} <- "
                f"{span.source_tool} ({span.source_event_id[:12]}...)"
            )
    else:
        console.print("[dim]this action is not a configured sensitive sink; no taint evaluation performed[/dim]")


if __name__ == "__main__":
    app()
