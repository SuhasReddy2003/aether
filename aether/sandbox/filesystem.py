"""Shadow filesystem: create/read/write/rename/delete confined to an
explicit temp root. Never touches any host path outside that root, and is
tested against path traversal, symlink escape, absolute paths, `..`, and
unicode tricks (tests/test_sandbox_filesystem.py).
"""
from __future__ import annotations

import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Self

from aether.core.errors import AetherSimulationError


class ShadowFilesystem:
    def __init__(self, root: str | Path | None = None) -> None:
        self._owns_root = root is None
        self.root = Path(root) if root else Path(tempfile.mkdtemp(prefix="aether_shadow_fs_"))
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        if self._owns_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _resolve(self, virtual_path: str) -> Path:
        """Resolve `virtual_path` against the root, rejecting anything that
        would escape it. This is the single security-critical function in
        this module — every public method goes through it.
        """
        if "\x00" in virtual_path:
            raise AetherSimulationError("null byte in path is not allowed")

        # Normalize unicode (NFKC) so lookalike/combining-character tricks
        # cannot be used to construct a path that *looks* safe but resolves
        # differently on different systems.
        normalized = unicodedata.normalize("NFKC", virtual_path)

        # Treat the incoming path as relative regardless of leading '/' or
        # drive letters — an "absolute path" from the agent's point of view
        # is still relative to the shadow root, never to the real host root.
        stripped = normalized.lstrip("/\\")
        # Reject Windows drive-letter absolute paths too (e.g. "C:\\Windows").
        if len(stripped) >= 2 and stripped[1] == ":":
            raise AetherSimulationError(f"drive-letter absolute paths are not allowed: {virtual_path!r}")

        candidate = (self.root / stripped).resolve()

        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise AetherSimulationError(
                f"path traversal blocked: {virtual_path!r} resolves outside the shadow root"
            ) from None

        # If any existing path component is a symlink, make sure it still
        # resolves inside the root (resolve() above already follows
        # symlinks, so a symlink pointing outside the root is already
        # caught by the relative_to check — this second check guards
        # against a symlink created *during* this call, i.e. a TOCTOU
        # variant, by re-checking after resolution).
        try:
            if candidate.is_symlink():
                real = candidate.resolve()
                try:
                    real.relative_to(self.root)
                except ValueError:
                    raise AetherSimulationError(f"symlink escape blocked: {virtual_path!r}") from None
        except OSError as exc:
            # Any OS-level failure while inspecting the candidate (name too
            # long, too many path components, etc.) is a rejection of the
            # path, never an uncaught crash out of the sandbox boundary.
            raise AetherSimulationError(f"path rejected by filesystem: {virtual_path!r} ({exc})") from exc

        # Hardlink defence (found by the Phase 3 self-audit): a hardlink placed
        # inside the root that points at a file OUTSIDE it looks like an
        # ordinary in-root file to every path check above. Regular files with
        # more than one link are refused. This cannot stop a hardlink created
        # by someone with host access from being *referenced*, only from
        # being read, written, or deleted through this sandbox.
        try:
            if candidate.is_file() and candidate.stat().st_nlink > 1:
                raise AetherSimulationError(f"multiply-linked file refused (possible hardlink escape): {virtual_path!r}")
        except OSError as exc:
            raise AetherSimulationError(f"path rejected by filesystem: {virtual_path!r} ({exc})") from exc

        return candidate

    # -- operations ---------------------------------------------------

    def create(self, virtual_path: str, content: str = "") -> dict[str, Any]:
        path = self._resolve(virtual_path)
        if path.exists():
            raise AetherSimulationError(f"already exists: {virtual_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return {"path": virtual_path, "bytes_written": len(content.encode("utf-8"))}

    def read(self, virtual_path: str) -> str:
        path = self._resolve(virtual_path)
        if not path.exists():
            raise AetherSimulationError(f"not found: {virtual_path}")
        return path.read_text()

    def write(self, virtual_path: str, content: str) -> dict[str, Any]:
        path = self._resolve(virtual_path)
        previous = path.read_text() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return {"path": virtual_path, "previous_content": previous, "bytes_written": len(content.encode("utf-8"))}

    def delete(self, virtual_path: str) -> dict[str, Any]:
        path = self._resolve(virtual_path)
        if not path.exists():
            raise AetherSimulationError(f"not found: {virtual_path}")
        previous = path.read_text() if path.is_file() else None
        path.unlink()
        return {"path": virtual_path, "previous_content": previous}

    def rename(self, src: str, dst: str) -> dict[str, Any]:
        src_path = self._resolve(src)
        dst_path = self._resolve(dst)
        if not src_path.exists():
            raise AetherSimulationError(f"not found: {src}")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        return {"src": src, "dst": dst}

    def exists(self, virtual_path: str) -> bool:
        return self._resolve(virtual_path).exists()

    def list_all(self) -> dict[str, str]:
        """Full snapshot of every file's content, keyed by path relative to
        the shadow root. Used for checkpoint/rollback content-hash
        comparisons."""
        result: dict[str, str] = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(self.root))
                result[rel] = p.read_text(errors="replace")
        return result
