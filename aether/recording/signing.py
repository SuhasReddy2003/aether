"""Ed25519 signing over the hash-chain head.

The signature does not replace the hash chain — it proves that whoever
holds the private key attested to a specific chain head at a specific time.
An attacker who can rewrite the whole chain AND has the private key can
still forge history; the key is the trust anchor and must be protected
(see docs/threat-model.md written in a later phase).
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


class KeyPair:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self.public_key: Ed25519PublicKey = private_key.public_key()

    @classmethod
    def generate(cls) -> KeyPair:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: Path) -> KeyPair:
        """Load a local Ed25519 key from `path`, or create+persist one.

        The key is stored raw-bytes-base64 with 0600 permissions. This is a
        local trust anchor, not a KMS-grade solution — that limitation is
        documented, not hidden.
        """
        if path.exists():
            raw = base64.b64decode(path.read_text().strip())
            private_key = Ed25519PrivateKey.from_private_bytes(raw)
            return cls(private_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            encoding=Encoding.Raw, format=PrivateFormat.Raw, encryption_algorithm=NoEncryption()
        )
        path.write_text(base64.b64encode(raw).decode("ascii"))
        try:
            path.chmod(0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX perms
        return cls(private_key)

    def public_key_b64(self) -> str:
        raw = self.public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def sign(self, message: bytes) -> str:
        signature = self._private_key.sign(message)
        return base64.b64encode(signature).decode("ascii")


def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        raw_pub = base64.b64decode(public_key_b64)
        raw_sig = base64.b64decode(signature_b64)
        public_key = Ed25519PublicKey.from_public_bytes(raw_pub)
        public_key.verify(raw_sig, message)
        return True
    except (InvalidSignature, ValueError):
        return False
