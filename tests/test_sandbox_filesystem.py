from __future__ import annotations

import os
from pathlib import Path

import pytest

from aether.core.errors import AetherSimulationError
from aether.sandbox.filesystem import ShadowFilesystem


@pytest.fixture()
def fs(tmp_path: Path) -> ShadowFilesystem:
    return ShadowFilesystem(root=tmp_path / "shadow_root")


def test_create_read_write_delete(fs: ShadowFilesystem) -> None:
    fs.create("notes/a.txt", "hello")
    assert fs.read("notes/a.txt") == "hello"
    result = fs.write("notes/a.txt", "world")
    assert result["previous_content"] == "hello"
    assert fs.read("notes/a.txt") == "world"
    deleted = fs.delete("notes/a.txt")
    assert deleted["previous_content"] == "world"
    assert not fs.exists("notes/a.txt")


def test_rename(fs: ShadowFilesystem) -> None:
    fs.create("a.txt", "x")
    fs.rename("a.txt", "b.txt")
    assert not fs.exists("a.txt")
    assert fs.read("b.txt") == "x"


def test_write_of_nonexistent_file_has_no_previous_content(fs: ShadowFilesystem) -> None:
    result = fs.write("new.txt", "content")
    assert result["previous_content"] is None


# --- security: path traversal / escape attempts -----------------------------

@pytest.mark.parametrize(
    "malicious_path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "../../../../../../etc/passwd",
        "a/../../b.txt",
        "a/b/../../../c.txt",
    ],
)
def test_dotdot_traversal_is_blocked(fs: ShadowFilesystem, malicious_path: str) -> None:
    with pytest.raises(AetherSimulationError, match="traversal|outside"):
        fs.create(malicious_path, "pwned")


def test_absolute_path_is_confined_to_root(fs: ShadowFilesystem, tmp_path: Path) -> None:
    # An "absolute" path from the agent's perspective must still resolve
    # inside the shadow root, never to the real host root.
    fs.create("/etc/passwd", "pwned")
    assert fs.exists("/etc/passwd")
    assert fs.read("/etc/passwd") == "pwned"
    # and the REAL /etc/passwd must be untouched
    real_passwd = Path("/etc/passwd")
    if real_passwd.exists():
        assert "pwned" not in real_passwd.read_text()


def test_windows_drive_letter_absolute_path_rejected(fs: ShadowFilesystem) -> None:
    with pytest.raises(AetherSimulationError):
        fs.create("C:\\Windows\\System32\\evil.txt", "pwned")


def test_null_byte_in_path_rejected(fs: ShadowFilesystem) -> None:
    with pytest.raises(AetherSimulationError):
        fs.create("a.txt\x00.png", "pwned")


def test_symlink_escape_is_blocked(fs: ShadowFilesystem, tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.txt"
    secret.write_text("top secret")

    # Manually create a symlink *inside* the shadow root pointing outside it,
    # simulating an attacker who got a symlink placed there some other way.
    link_path = fs.root / "escape_link"
    os.symlink(outside_dir, link_path)

    with pytest.raises(AetherSimulationError, match="traversal|escape|outside"):
        fs.read("escape_link/secret.txt")


def test_unicode_normalization_trick_still_confined(fs: ShadowFilesystem) -> None:
    # NFKC-normalizable fullwidth characters should not enable escaping the
    # root; whatever they normalize to must still resolve inside root or be
    # treated as an ordinary (safe) filename.
    weird_path = "\uFF0E\uFF0E/outside.txt"  # fullwidth ".." + slash
    with pytest.raises(AetherSimulationError):
        fs.create(weird_path, "pwned")


def test_oversized_path_component_does_not_crash(fs: ShadowFilesystem) -> None:
    long_name = "a" * 500 + ".txt"
    # Should either succeed within the sandbox or raise a clean AetherSimulationError,
    # never an unhandled OSError bubbling out of the sandbox boundary.
    try:
        fs.create(long_name, "x")
    except AetherSimulationError:
        pass
    except OSError:
        pytest.fail("raw OSError leaked out of the sandbox for an oversized path component")


def test_list_all_reflects_only_files_inside_root(fs: ShadowFilesystem) -> None:
    fs.create("a.txt", "1")
    fs.create("dir/b.txt", "2")
    snapshot = fs.list_all()
    assert snapshot == {"a.txt": "1", "dir/b.txt": "2"}


def test_close_removes_owned_temp_root() -> None:
    fs = ShadowFilesystem()  # owns its own temp root
    root = fs.root
    fs.create("x.txt", "y")
    assert root.exists()
    fs.close()
    assert not root.exists()


def test_close_does_not_remove_explicit_root(tmp_path: Path) -> None:
    explicit_root = tmp_path / "explicit"
    fs = ShadowFilesystem(root=explicit_root)
    fs.create("x.txt", "y")
    fs.close()
    assert explicit_root.exists()  # caller owns this root, Aether must not delete it
