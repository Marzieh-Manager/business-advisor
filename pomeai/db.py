"""SQLite storage.

Milestones 1-2 stored notes in a JSONL file. This module takes over, keeping the
schema simple enough to move to Postgres later if it ever needs to.

`received_at` is kept as a readable UTC ISO string, but range queries run against
`received_at_epoch`. Comparing timestamps as text only works while every row uses
an identical offset format, and that is too easy to break by accident.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pomeai.config import Config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id                 TEXT    PRIMARY KEY,
    received_at             TEXT    NOT NULL,
    received_at_epoch       INTEGER NOT NULL,
    duration_seconds        INTEGER NOT NULL DEFAULT 0,
    audio_filename          TEXT    NOT NULL,
    telegram_message_id     INTEGER,
    telegram_file_unique_id TEXT,
    transcript              TEXT,
    transcript_provider     TEXT,
    source                  TEXT    NOT NULL DEFAULT 'voice',
    telegram_chat_id        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_notes_received_at_epoch
    ON notes (received_at_epoch);

-- Every recommendation the advisor has ever made, and what became of it.
--
-- This is what separates an advisor from a weekly commentator: without it the
-- system meets the owner for the first time every Monday. `topic` is the thread
-- a decision belongs to, so recurrence ("the fourth week running") can be
-- counted in code rather than guessed at by the model.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id      TEXT    PRIMARY KEY,
    week_start       TEXT    NOT NULL,   -- the report week that raised it
    created_at       TEXT    NOT NULL,
    topic            TEXT    NOT NULL,   -- slug, e.g. 'warehouse-hire'
    subject          TEXT    NOT NULL,   -- short label, shown in follow-up
    detail           TEXT    NOT NULL,   -- what he was actually told to do
    rank             INTEGER,            -- 1-3 for a priority, NULL otherwise
    status           TEXT    NOT NULL DEFAULT 'open',
    status_note      TEXT,
    resolved_week    TEXT,               -- the week its status was last set
    report_filename  TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_topic  ON decisions (topic);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions (status);
CREATE INDEX IF NOT EXISTS idx_decisions_week   ON decisions (week_start);

-- Small scraps the bot needs to remember between runs - currently only when it
-- last nudged the owner, so a quiet spell produces one message rather than one
-- a day. Deliberately untyped: this is not data, it is bookkeeping.
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);
"""

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` is a no-op
# on an existing table, so new columns need an explicit ALTER.
MIGRATIONS = (
    ("source", "ALTER TABLE notes ADD COLUMN source TEXT NOT NULL DEFAULT 'voice'"),
    ("telegram_chat_id", "ALTER TABLE notes ADD COLUMN telegram_chat_id INTEGER"),
)

LEGACY_INDEX_FILENAME = "notes.jsonl"


def to_epoch(iso_timestamp: str) -> int:
    """Seconds since the epoch, UTC, from an ISO 8601 string."""
    return int(datetime.fromisoformat(iso_timestamp).astimezone(timezone.utc).timestamp())


def connect(config: Config) -> sqlite3.Connection:
    """Open the database, creating the schema and importing legacy data once."""
    fresh = not config.db_path.exists()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    # Referential sanity and safer concurrent reads while the bot is running.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()

    if fresh:
        _import_legacy_jsonl(conn, config)

    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any columns introduced after a database was first created."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
    for column, statement in MIGRATIONS:
        if column not in existing:
            log.info("migrating notes table: adding %s", column)
            conn.execute(statement)


@contextmanager
def session(config: Config) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, and always close it.

    `sqlite3.Connection.__exit__` commits but leaves the handle open, which
    leaks file descriptors when the bot runs for a week.
    """
    conn = connect(config)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _import_legacy_jsonl(conn: sqlite3.Connection, config: Config) -> int:
    """Move a pre-SQLite `notes.jsonl` into the database, once.

    The old file is renamed rather than deleted - if anything about the import
    is wrong, the original captures are still on disk.
    """
    legacy: Path = config.data_dir / LEGACY_INDEX_FILENAME
    if not legacy.exists():
        return 0

    imported = 0
    for line_no, line in enumerate(legacy.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            conn.execute(
                """
                INSERT OR IGNORE INTO notes (
                    note_id, received_at, received_at_epoch, duration_seconds,
                    audio_filename, telegram_message_id, telegram_file_unique_id,
                    transcript, transcript_provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["note_id"],
                    row["received_at"],
                    to_epoch(row["received_at"]),
                    row.get("duration_seconds", 0),
                    row["audio_filename"],
                    row.get("telegram_message_id"),
                    row.get("telegram_file_unique_id"),
                    row.get("transcript"),
                    row.get("transcript_provider"),
                ),
            )
            imported += 1
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning("skipping %s line %d during import: %s", legacy.name, line_no, exc)

    conn.commit()
    legacy.rename(legacy.with_suffix(".jsonl.imported"))
    log.info("imported %d note(s) from %s into SQLite", imported, legacy.name)
    return imported
