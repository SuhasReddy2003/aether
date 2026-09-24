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

from aether.core.errors import AetherCassetteError, AetherIntegrityError
from aether.recording.cassette import export_cassette, import_cassette
from aether.recording.hashchain import verify_chain
from aether.recording.recorder import DEFAULT_DATA_DIR
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
    sig_ok = True
    if sig:
        from aether.recording.signing import verify_signature

        sig_ok = verify_signature(sig["public_key_b64"], all_events[-1].hash.encode("utf-8"), sig["signature_b64"])

    if sig_ok:
        console.print(
            Panel(
                f"[bold green]VALID[/bold green]\n{len(all_events)} events verified\nHead hash: {all_events[-1].hash}",
                title=f"aether verify {run_id}",
                border_style="green",
            )
        )
    else:
        console.print(Panel("[bold red]INVALID SIGNATURE[/bold red]", title=f"aether verify {run_id}", border_style="red"))
        raise typer.Exit(1)


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
def import_cmd(path: Path) -> None:
    """Import a .aether cassette file, verifying it first."""
    storage = _storage()
    try:
        run_id = import_cassette(storage, path, verify=True)
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


if __name__ == "__main__":
    app()
