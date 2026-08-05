"""
tests/conftest.py — shared pytest fixtures.

The autouse isolated_storage fixture redirects both persistence layers
(store/db.py's SQLite file and memory.py's JSON file) to a per-test temp
path, so no test can ever touch the real data/jarvis.db or
jarvis_memory.json — those hold real personal data (tasks, expenses, mood).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory as memory_module
from store import db as store_db


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Redirect the SQLite store and JSON memory file to a temp path for every test."""
    monkeypatch.setattr(store_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store_db, "DB_FILE", tmp_path / "test_jarvis.db")
    store_db.init_db()

    monkeypatch.setattr(memory_module, "MEMORY_FILE", tmp_path / "test_memory.json")
    monkeypatch.setattr(memory_module, "_memory", {})

    yield
