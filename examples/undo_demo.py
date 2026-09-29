"""Undo demo (Phase 2): initial state -> agent mutates records -> snapshot ->
dangerous action detected -> rollback -> verify original state restored
exactly (by content hash).

Everything here is real: the CRM state is a real JSON file on disk, the
file operations run through a real (sandboxed) filesystem, and the payment
is the only thing that stays simulated (irreversible, by design, per the
SECURITY RULES).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether import Aether
from aether.core.side_effects import SideEffectType
from aether.sandbox.filesystem import ShadowFilesystem
from aether.state.snapshot import content_hash
from aether.state.world import WorldState
from aether.undo.builtin import register_builtin_compensators, world_restore_from_result
from aether.undo.compensation import CompensationRegistry
from aether.undo.rollback import RollbackEngine

DATA_DIR = Path.home() / ".aether"

aether = Aether(data_dir=DATA_DIR)


def build_tools(run_id: str, fs: ShadowFilesystem, world: WorldState):
    @aether.tool(
        name="crm.update",
        side_effects=[SideEffectType.UPDATE],
        reversible=True,
        compensation="crm.restore_previous",
    )
    def crm_update(customer_id: str, note: str) -> dict:
        data = world.load()
        previous = data.get(customer_id)
        data[customer_id] = {"note": note}
        world.save(data)
        return {"customer_id": customer_id, "note": note, "previous": previous}

    @aether.tool(
        name="fs.write_file",
        side_effects=[SideEffectType.CREATE],
        reversible=True,
        compensation="fs.restore_write",
    )
    def write_file(virtual_path: str, content: str) -> dict:
        exists = fs.exists(virtual_path)
        if exists:
            return fs.write(virtual_path, content)
        result = fs.create(virtual_path, content)
        result["previous_content"] = None
        return result

    @aether.tool(
        name="payment.create",
        side_effects=[SideEffectType.FINANCIAL, SideEffectType.EXTERNAL_CALL, SideEffectType.IRREVERSIBLE],
    )
    def create_payment(amount: float, to: str) -> dict:
        # SIMULATED ONLY.
        return {"payment_id": "sim_pay_undo_demo", "amount": amount, "to": to, "status": "simulated"}

    return crm_update, write_file, create_payment


def main() -> None:
    from aether.core.action import new_id

    run_id = new_id("undo_demo")
    fs_root = DATA_DIR / "shadow_fs" / run_id
    fs = ShadowFilesystem(root=fs_root)
    world = WorldState(data_dir=DATA_DIR, run_id=run_id)

    # start clean for a repeatable demo
    world.save({})
    for f in list(fs.list_all()):
        fs.delete(f)

    crm_update, write_file, create_payment = build_tools(run_id, fs, world)

    with aether.run(agent="support_agent", goal="Resolve refund", run_id=run_id):
        crm_update("cust_1001", note="Initial review: refund approved for $84.00")
        write_file("refund_notice.txt", "Refund of $84.00 approved for cust_1001.")

        checkpoint_world_hash = world.content_hash()
        checkpoint_fs_hash = content_hash(fs.list_all())
        print(f"Checkpoint taken. world_hash={checkpoint_world_hash[:16]}... fs_hash={checkpoint_fs_hash[:16]}...")

        # Now something goes wrong: the note and file get overwritten with a
        # much larger, suspicious amount, and a payment is attempted.
        crm_update("cust_1001", note="CORRECTED: refund should be $2,840.00, urgent!")
        write_file("refund_notice.txt", "Refund of $2,840.00 approved for cust_1001. URGENT.")
        create_payment(amount=2840.00, to="acct_991")

    print(f"\nBefore rollback: CRM note = {world.load()['cust_1001']['note']!r}")
    print(f"Before rollback: file content = {fs.read('refund_notice.txt')!r}")

    registry = CompensationRegistry()
    register_builtin_compensators(registry)
    registry.register("crm.restore_previous", world_restore_from_result("customer_id"))
    engine = RollbackEngine(aether.recorder, registry)

    # roll back everything after step 2 (the initial CRM update + file write)
    report = engine.rollback_to(run_id, target_step=2, context={"fs": fs, "world": world})

    print("\nRollback report:")
    for r in report.results:
        print(f"  step {r.step:>2} {r.tool:<20} -> {r.outcome:<12} {r.detail[:70]}")

    restored_world_hash = world.content_hash()
    restored_fs_hash = content_hash(fs.list_all())

    print(f"\nAfter rollback: CRM note = {world.load()['cust_1001']['note']!r}")
    print(f"After rollback: file content = {fs.read('refund_notice.txt')!r}")

    assert restored_world_hash == checkpoint_world_hash, "world state did not restore exactly!"
    assert restored_fs_hash == checkpoint_fs_hash, "filesystem state did not restore exactly!"
    print("\nVERIFIED: restored state content-hash matches checkpoint exactly (byte for byte).")

    payment_results = [r for r in report.results if r.tool == "payment.create"]
    assert payment_results and payment_results[0].outcome == "irreversible"
    print(f"VERIFIED: payment.create was correctly reported as irreversible: {payment_results[0].detail}")

    fs.close()
    print(f"\nInspect this run with:\n  aether inspect {run_id}\n  aether verify {run_id}")


if __name__ == "__main__":
    main()
