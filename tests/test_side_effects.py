from __future__ import annotations

import pytest

from aether.core.side_effects import SideEffect, SideEffectType


def test_reversible_requires_compensation() -> None:
    with pytest.raises(ValueError, match="compensation"):
        SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation=None)


def test_irreversible_type_cannot_claim_reversible() -> None:
    with pytest.raises(ValueError, match="IRREVERSIBLE"):
        SideEffect(type=SideEffectType.IRREVERSIBLE, reversible=True, compensation="whatever")


def test_default_is_not_reversible() -> None:
    effect = SideEffect(type=SideEffectType.WRITE)
    assert effect.reversible is False
    assert effect.compensation is None


def test_valid_reversible_effect() -> None:
    effect = SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation="restore_previous")
    assert effect.reversible is True
