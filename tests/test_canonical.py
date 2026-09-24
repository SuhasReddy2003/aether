from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aether.core.canonical import canonical_json

json_scalars = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.text())
json_values = st.recursive(
    json_scalars,
    lambda children: st.one_of(st.lists(children, max_size=5), st.dictionaries(st.text(), children, max_size=5)),
    max_leaves=20,
)


@given(json_values)
def test_canonical_json_deterministic_on_same_value(value: object) -> None:
    assert canonical_json(value) == canonical_json(value)


def test_canonical_json_ignores_dict_key_order() -> None:
    a = {"b": 1, "a": 2, "c": {"y": 1, "x": 2}}
    b = {"a": 2, "c": {"x": 2, "y": 1}, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_canonical_json_rejects_infinity() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("inf")})


def test_canonical_json_no_insignificant_whitespace() -> None:
    out = canonical_json({"a": 1, "b": [1, 2, 3]})
    assert b" " not in out
