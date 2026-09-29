from __future__ import annotations

from aether.core.side_effects import SideEffectType
from aether.sandbox.network import ShadowNetwork


def test_simulate_call_is_recorded_and_never_reversible_by_default() -> None:
    net = ShadowNetwork()
    effect = net.simulate_call("https://api.example.test/webhook", "notify downstream system")
    assert effect.kind == "external_call"
    assert effect.reversible is False
    assert len(net.calls) == 1


def test_simulate_call_can_be_marked_reversible() -> None:
    net = ShadowNetwork()
    effect = net.simulate_call("https://api.example.test/cache", "warm a cache entry", reversible=True)
    assert effect.reversible is True
    side_effect = effect.to_side_effect()
    assert side_effect.reversible is True
    assert side_effect.compensation == "external.manual_reversal"


def test_simulate_payment_is_never_reversible() -> None:
    net = ShadowNetwork()
    effect = net.simulate_payment("acct_991", amount=2840.00, description="refund")
    assert effect.kind == "money_movement"
    assert effect.reversible is False  # payments are never faked as reversible
    assert effect.amount == 2840.00
    side_effect = effect.to_side_effect()
    assert side_effect.type == SideEffectType.FINANCIAL
    assert side_effect.reversible is False
    assert side_effect.compensation is None


def test_simulate_communication_is_never_reversible() -> None:
    net = ShadowNetwork()
    effect = net.simulate_communication("customer@example.test", "sent refund confirmation email")
    assert effect.kind == "communication"
    assert effect.reversible is False
    side_effect = effect.to_side_effect()
    assert side_effect.type == SideEffectType.COMMUNICATION


def test_no_real_network_module_is_imported() -> None:
    # Structural guarantee: this module must never import a real HTTP/socket
    # library, since it exists specifically to make real network calls
    # impossible to make by accident.
    import ast
    import inspect

    import aether.sandbox.network as network_module

    source = inspect.getsource(network_module)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])

    forbidden = {"socket", "requests", "httpx", "urllib", "http"}
    assert not (imported_names & forbidden), f"shadow network module imports real network libs: {imported_names & forbidden}"


def test_calls_accumulate_across_multiple_simulations() -> None:
    net = ShadowNetwork()
    net.simulate_call("target_a", "desc a")
    net.simulate_payment("target_b", 10.0)
    net.simulate_communication("target_c", "desc c")
    assert len(net.calls) == 3
