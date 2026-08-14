"""tests/test_backup.py — nightly SQLite snapshot + retention pruning, and the
manual /backup command wrapper in modules/system.py."""

from store import db as store_db


def test_backup_db_creates_a_snapshot_file():
    dest = store_db.backup_db()

    assert dest.exists()
    assert dest.parent == store_db.DATA_DIR / "backups"
    assert dest.suffix == ".db"


def test_backup_db_snapshot_contains_current_data():
    with store_db.get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (chat_id, title, status, created_at) VALUES (?, ?, 'pending', ?)",
            ("owner", "buy milk", "2026-01-01T00:00:00"),
        )

    dest = store_db.backup_db()

    import sqlite3

    conn = sqlite3.connect(dest)
    try:
        row = conn.execute("SELECT title FROM tasks WHERE chat_id = 'owner'").fetchone()
    finally:
        conn.close()
    assert row == ("buy milk",)


def test_backup_db_prunes_beyond_retention_count(monkeypatch):
    monkeypatch.setattr(store_db, "BACKUP_RETENTION_COUNT", 2)

    import datetime

    backup_dir = store_db.DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ("jarvis-20260101-000000.db", "jarvis-20260102-000000.db"):
        (backup_dir / name).write_bytes(b"")

    store_db.backup_db()

    remaining = sorted(p.name for p in backup_dir.glob("jarvis-*.db"))
    assert len(remaining) == 2


def test_run_backup_reports_the_new_file(monkeypatch):
    from modules import system

    message = system.run_backup("owner", [])

    assert "Backed up to" in message
    assert "KB" in message
