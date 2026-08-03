"""
store/db.py — SQLite connection + schema for structured Jarvis data.

Flat JSON (memory.py) stays for preferences/facts; anything that needs
querying, filtering by status/date, or per-user rows lives here instead.
See JARVIS_V2_PLAN.md §4 for the rationale.

Usage
─────
  from store import db
  db.init_db()                 # call once at startup — creates tables if absent
  with db.get_connection() as conn:
      conn.execute(...)
"""

import logging
import sqlite3
from contextlib import contextmanager

from config import DATA_DIR, DB_FILE

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    due_at TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    message TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mood_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    mood TEXT NOT NULL,
    energy INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS habit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    habit TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    message TEXT NOT NULL,
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_fired_date TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    amount REAL,
    currency TEXT NOT NULL DEFAULT 'BTN',
    recipient TEXT,
    remarks TEXT,
    category TEXT,
    raw_ocr_text TEXT,
    image_filename TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_chat_status ON tasks (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (delivered, fire_at);
CREATE INDEX IF NOT EXISTS idx_mood_log_chat ON mood_log (chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_habit_log_chat_habit ON habit_log (chat_id, habit, completed_at);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_symbol_time ON price_snapshots (symbol, recorded_at);
CREATE INDEX IF NOT EXISTS idx_expenses_chat_created ON expenses (chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_daily_reminders_chat_enabled ON daily_reminders (chat_id, enabled);
"""

# Allowed task status transitions — no arbitrary status writes (AGENTS.md §14).
ALLOWED_TASK_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


def init_db() -> None:
    """Create the data directory and tables if they don't already exist.

    Safe to call on every startup — CREATE TABLE IF NOT EXISTS is idempotent.
    Also applies small additive column migrations for tables created before
    that column existed (CREATE TABLE IF NOT EXISTS alone can't do this).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        _ensure_column(conn, "expenses", "category", "TEXT")
    logger.info("Store initialised at %s", DB_FILE)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, sql_type: str) -> None:
    """Add `column` to `table` if it doesn't already exist (additive migration only)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
        logger.info("Migrated: added column %s.%s", table, column)


@contextmanager
def get_connection():
    """Yield a SQLite connection, committing on success and closing after.

    Row access is by column name (sqlite3.Row) so callers can do row["title"]
    instead of positional indexing.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
