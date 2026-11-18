"""Voice-note storage.

Backed by SQLite (see `pomeai.db`). The public API here - `append_note`,
`update_note`, `load_notes` - is unchanged from the JSONL version, so the bot
and the transcription layer did not have to change when storage moved.

Timestamps are stored in UTC. The business's weekday is derived on read from
`REPORT_TIMEZONE`, so changing that setting never leaves stale weekday labels
behind in the data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pomeai.config import Config
from pomeai.db import session, to_epoch
from pomeai.formatting import weekday
from pomeai.weeks import Week

_COLUMNS = (
    "note_id, received_at, duration_seconds, audio_filename, "
    "telegram_message_id, telegram_file_unique_id, transcript, transcript_provider, "
    "source, telegram_chat_id"
)


@dataclass
class VoiceNote:
    """One captured voice note."""

    note_id: str
    received_at: str  # UTC, ISO 8601
    duration_seconds: int
    audio_filename: str  # relative to config.audio_dir, so the data stays portable
    telegram_message_id: int | None = None
    telegram_file_unique_id: str | None = None

    # Filled in at Milestone 2. One row per note throughout, rather than a
    # second parallel store for transcripts.
    transcript: str | None = None
    transcript_provider: str | None = None

    # How the note arrived: 'voice' | 'text' | 'import'. Recorded so the
    # validation phase can tell whether typed notes produce weaker reports
    # than spoken ones - typing invites self-editing, speaking does not.
    source: str = "voice"

    # Stored so the owner's chat id can be recovered from the data if the
    # setup run's terminal output is lost.
    telegram_chat_id: int | None = None

    @property
    def is_text(self) -> bool:
        """A typed note - already "transcribed", with no audio to play back."""
        return self.source == "text"

    @property
    def received_at_utc(self) -> datetime:
        return datetime.fromisoformat(self.received_at)

    def local_time(self, tz_name: str) -> datetime:
        return self.received_at_utc.astimezone(ZoneInfo(tz_name))

    def weekday(self, tz_name: str) -> str:
        return weekday(self.local_time(tz_name))

    def audio_path(self, config: Config) -> Path:
        return config.audio_dir / self.audio_filename


def make_note_id(received_at: datetime, unique_suffix: str) -> str:
    """A sortable, filesystem-safe id: `20260812T091500Z-<suffix>`."""
    stamp = received_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch for ch in unique_suffix if ch.isalnum() or ch in "-_")
    return f"{stamp}-{safe}" if safe else stamp


def _to_note(row: sqlite3.Row) -> VoiceNote:
    return VoiceNote(**{key: row[key] for key in row.keys()})


def append_note(config: Config, note: VoiceNote) -> None:
    """Store a newly captured note."""
    with session(config) as conn:
        conn.execute(
            f"""
            INSERT INTO notes ({_COLUMNS}, received_at_epoch)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note.note_id,
                note.received_at,
                note.duration_seconds,
                note.audio_filename,
                note.telegram_message_id,
                note.telegram_file_unique_id,
                note.transcript,
                note.transcript_provider,
                note.source,
                note.telegram_chat_id,
                to_epoch(note.received_at),
            ),
        )


def update_note(config: Config, updated: VoiceNote) -> None:
    """Replace one note, matched on `note_id`.

    Only the mutable fields are written - a note's identity, timing and audio
    never change after capture.
    """
    with session(config) as conn:
        cursor = conn.execute(
            """
            UPDATE notes
               SET duration_seconds = ?, transcript = ?, transcript_provider = ?
             WHERE note_id = ?
            """,
            (
                updated.duration_seconds,
                updated.transcript,
                updated.transcript_provider,
                updated.note_id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"no note with id {updated.note_id!r} in the database")


def load_notes(
    config: Config,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[VoiceNote]:
    """Notes in `[start, end)`, oldest first. Both bounds are optional."""
    clauses, params = [], []
    if start is not None:
        clauses.append("received_at_epoch >= ?")
        params.append(int(start.astimezone(timezone.utc).timestamp()))
    if end is not None:
        clauses.append("received_at_epoch < ?")
        params.append(int(end.astimezone(timezone.utc).timestamp()))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(config) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM notes {where} ORDER BY received_at_epoch ASC",
            params,
        ).fetchall()

    return [_to_note(row) for row in rows]


def load_week(config: Config, week: Week) -> list[VoiceNote]:
    """Every note in one reporting week - what the weekly report is built from."""
    return load_notes(config, start=week.start, end=week.end)
