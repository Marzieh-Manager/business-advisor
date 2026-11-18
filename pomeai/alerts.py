"""Outbound operational alerts.

Every failure path in this system currently ends at a log line, on a console
nobody is watching. The dangerous case is transcription: the owner is told
"Saved", the note is never transcribed, and Monday's report simply does not
contain it. He was told it arrived; nobody learns it did not.

This module is the other direction of the Telegram channel. `TELEGRAM_ALERT_CHAT_ID`
is an operator's chat - it is **never** added to the inbound allow-list in
`bot.build_application`, so being alerted does not grant the ability to send
notes into the owner's week.

One rule governs the whole module: **an alert may never break the thing it is
reporting on.** Every entry point swallows every exception. A note that saved but
failed to alert is a good outcome; an alert that lost a note is not.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from pomeai.config import Config
from pomeai.db import session

log = logging.getLogger(__name__)

# Telegram rejects messages over 4096 characters. Tracebacks get long.
MAX_LENGTH = 3800


def _truncate(text: str) -> str:
    if len(text) <= MAX_LENGTH:
        return text
    return text[:MAX_LENGTH] + "\n[...truncated]"


async def send(config: Config, text: str, *, chat_id: str | None = None) -> bool:
    """Send one alert. Returns whether it went out; never raises.

    `chat_id` overrides the destination - used for the nudge, which goes to the
    owner rather than the operator.
    """
    destination = chat_id or config.telegram_alert_chat_id
    if not destination:
        log.info("alert not sent (no TELEGRAM_ALERT_CHAT_ID): %s", text.splitlines()[0])
        return False
    if not config.telegram_bot_token:
        log.warning("alert not sent (no bot token): %s", text.splitlines()[0])
        return False

    try:
        from telegram import Bot

        await Bot(config.telegram_bot_token).send_message(
            chat_id=int(destination), text=_truncate(text)
        )
        return True
    except Exception:
        # Deliberately broad. Whatever went wrong here matters less than the
        # caller, which is usually already handling a failure of its own.
        log.exception("could not deliver alert")
        return False


def send_sync(config: Config, text: str, *, chat_id: str | None = None) -> bool:
    """`send` for callers outside an event loop, such as the CLI."""
    try:
        return asyncio.run(send(config, text, chat_id=chat_id))
    except RuntimeError:
        # Already inside a loop - the caller should have awaited `send`.
        log.warning("send_sync called from a running event loop; use `send`")
        return False
    except Exception:
        log.exception("could not deliver alert")
        return False


# --- small persistent scraps -------------------------------------------------

def get_state(config: Config, key: str) -> str | None:
    """Read one bookkeeping value, or None."""
    try:
        with session(config) as conn:
            row = conn.execute(
                "SELECT value FROM state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None
    except Exception:
        log.exception("could not read state %r", key)
        return None


def set_state(config: Config, key: str, value: str | None) -> None:
    """Write one bookkeeping value. Never raises into the caller."""
    try:
        with session(config) as conn:
            conn.execute(
                "INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
    except Exception:
        log.exception("could not write state %r", key)
