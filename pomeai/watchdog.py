"""Notices when the owner has gone quiet.

Silence means one of two things and they are indistinguishable from inside: the
bot is broken, or he has stopped using it. Both are fatal to the one thing this
phase has to prove, and both are currently invisible.

The honest limitation, stated where it cannot be missed: **this runs inside the
bot, so it cannot detect the bot being dead.** A sleeping laptop takes the
watchdog down with it, and the alert that matters most is exactly the one that
cannot be sent. Until the bot lives on a supervised server, `cli status` is the
only real cover.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pomeai.config import Config

log = logging.getLogger(__name__)

# Set when the owner is nudged, cleared when he next sends something, so a quiet
# spell produces one message rather than one a day.
NUDGE_STATE_KEY = "last_nudge_week"

NUDGE_TEXT = (
    "Quiet week so far \U0001F44B\n\n"
    "Anything on your mind - a problem, a decision, something that came up with "
    "the team - send it over and it goes into Monday's report."
)


def days_quiet(last_note_at: datetime | None, now: datetime) -> int | None:
    """Whole days since the last note. None when nothing has ever arrived."""
    if last_note_at is None:
        return None
    return max(0, (now - last_note_at).days)


def should_nudge_owner(
    *,
    quiet_days: int | None,
    threshold: int,
    now: datetime,
    already_nudged_on: str | None,
) -> bool:
    """Whether to send the owner one soft nudge.

    Four conditions, each guarding against a way this could become annoying:
    he has sent something before, he has been quiet long enough, it is a weekday,
    and he has not already been nudged during this spell.
    """
    if quiet_days is None:
        # Never sent anything. Nudging someone who has not started is spam.
        return False
    if quiet_days < threshold:
        return False
    if now.weekday() >= 5:
        # Saturday or Sunday. Silence at the weekend is not a signal.
        return False
    return already_nudged_on != now.date().isoformat()


def operator_report(
    *, quiet_days: int | None, last_note_at: datetime | None, threshold: int
) -> str:
    """The factual line the operator gets. Written to be scannable on a phone."""
    if quiet_days is None:
        return (
            "⚠ No voice notes have ever been received.\n"
            "Either the owner has not started, or the bot is not reaching him."
        )
    when = last_note_at.strftime("%a %d %b, %H:%M UTC") if last_note_at else "unknown"
    return (
        f"⚠ Quiet for {quiet_days} day(s) (threshold {threshold}).\n"
        f"Last note: {when}.\n"
        "Either he has gone quiet, or capture is broken - check `cli status`."
    )


async def run_check(config: Config, *, now: datetime | None = None) -> dict:
    """One watchdog pass. Returns what it did, for logging and for tests."""
    from pomeai import alerts
    from pomeai.notes import load_notes

    moment = now or datetime.now(timezone.utc)
    result = {"quiet_days": None, "alerted_operator": False, "nudged_owner": False}

    try:
        notes = load_notes(config)
    except Exception:
        log.exception("watchdog could not read notes")
        return result

    last_at = None
    if notes:
        try:
            last_at = datetime.fromisoformat(notes[-1].received_at).astimezone(timezone.utc)
        except (ValueError, TypeError):
            log.warning("watchdog could not parse %r", notes[-1].received_at)

    quiet = days_quiet(last_at, moment)
    result["quiet_days"] = quiet

    if quiet is not None and quiet < config.silence_alert_days:
        # He is active. Clear the nudge marker so the next spell gets one.
        alerts.set_state(config, NUDGE_STATE_KEY, None)
        return result

    result["alerted_operator"] = await alerts.send(
        config,
        operator_report(
            quiet_days=quiet, last_note_at=last_at, threshold=config.silence_alert_days
        ),
    )

    if should_nudge_owner(
        quiet_days=quiet,
        threshold=config.silence_alert_days,
        now=moment,
        already_nudged_on=alerts.get_state(config, NUDGE_STATE_KEY),
    ):
        sent = await alerts.send(
            config, NUDGE_TEXT, chat_id=config.telegram_owner_chat_id
        )
        if sent:
            alerts.set_state(config, NUDGE_STATE_KEY, moment.date().isoformat())
        result["nudged_owner"] = sent

    return result
