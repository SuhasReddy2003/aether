from __future__ import annotations

from pathlib import Path

import pytest

from aether.recording.recorder import Recorder
from aether.runtime.interceptor import Aether
from aether.storage.sqlite import SQLiteStorage


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "aether_data"
    d.mkdir()
    return d


@pytest.fixture()
def storage(data_dir: Path) -> SQLiteStorage:
    return SQLiteStorage(data_dir / "aether.db")


@pytest.fixture()
def recorder(data_dir: Path) -> Recorder:
    return Recorder(data_dir=data_dir)


@pytest.fixture()
def aether_instance(data_dir: Path) -> Aether:
    return Aether(data_dir=data_dir)
