from __future__ import annotations

from pathlib import Path

from aether.recording.signing import KeyPair, verify_signature


def test_sign_and_verify_roundtrip() -> None:
    kp = KeyPair.generate()
    msg = b"hello aether"
    sig = kp.sign(msg)
    assert verify_signature(kp.public_key_b64(), msg, sig)


def test_verify_fails_on_wrong_message() -> None:
    kp = KeyPair.generate()
    sig = kp.sign(b"message a")
    assert not verify_signature(kp.public_key_b64(), b"message b", sig)


def test_verify_fails_on_wrong_key() -> None:
    kp1 = KeyPair.generate()
    kp2 = KeyPair.generate()
    sig = kp1.sign(b"hello")
    assert not verify_signature(kp2.public_key_b64(), b"hello", sig)


def test_key_persists_across_loads(tmp_path: Path) -> None:
    key_path = tmp_path / "signing_key"
    kp1 = KeyPair.load_or_create(key_path)
    kp2 = KeyPair.load_or_create(key_path)
    assert kp1.public_key_b64() == kp2.public_key_b64()


def test_verify_signature_rejects_garbage() -> None:
    assert not verify_signature("not-base64-!!!", b"msg", "also-not-base64-!!!")
