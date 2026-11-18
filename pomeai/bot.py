"""Telegram voice capture.

Telegram is the capture channel because it has one-tap voice recording, is
already on the owner's phone, and needs no front-end code. This is a
single-user system: the bot answers one chat id and ignores everything else.

Milestone 1 stores the raw audio only. Transcription arrives at Milestone 2.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from pomeai import alerts
from pomeai.config import Config
from pomeai.notes import VoiceNote, append_note, load_notes, make_note_id
from pomeai.formatting import duration

log = logging.getLogger(__name__)

# Telegram sends voice notes as OGG/Opus.
VOICE_SUFFIX = ".ogg"

WELCOME = (
    "Hi \U0001F44B\n\n"
    "I'm not a chatbot and I won't answer you here. My job is different:\n\n"
    "\U0001F3A4 During the week, whenever something is on your mind - a problem, "
    "an idea, a decision you keep putting off, something that came up with one "
    "of the team - send me a voice note. It doesn't need to be tidy.\n\n"
    "\U0001F4CA Monday morning I read them all together and send you a full "
    "analysis: your biggest issue, your best opportunity, and three priorities "
    "for the week ahead.\n\n"
    "That's it. Send a voice note and we're started."
)

# The bot's whole job this milestone is to make sending a note feel weightless,
# so the confirmation stays one line.
CONFIRMED = "\u2705 Saved - {duration} \u00b7 note {count} this week"

# Nudges back toward voice without refusing the note that just arrived.
CONFIRMED_TEXT = (
    "\u2705 Saved - note {count} this week\n"
    "(a voice note works better - easier for you, and you say more)"
)

# Only reaches photos, stickers and the like - voice, audio files and text are
# all stored. Redirects without reading as a rejection.
NOT_VOICE = (
    "I didn't save that - I take voice notes, audio files and text.\n\n"
    "If something's on your mind, just say it or type it. Monday morning I'll "
    "analyse the week together."
)

SAVE_FAILED = "\u274C Couldn't save that. Please send it again."


class BotConfigError(RuntimeError):
    """The bot cannot start with the current configuration."""


# Exported voice notes carry their recording time in the filename. WhatsApp
# writes `AUDIO-2026-08-21-09-08-02.m4a`; some clients use
# `WhatsApp Audio 2026-08-21 at 09.08.02.m4a`.
_FILENAME_STAMP = re.compile(
    r"(20\d{2})[-_](\d{2})[-_](\d{2})"        # date
    r"(?:[-_ ]|\s+at\s+)"                      # separator
    r"(\d{2})[-_.](\d{2})[-_.](\d{2})"        # time
)


def _timestamp_from_filename(name: str | None, tz_name: str) -> datetime | None:
    """Recording time embedded in an exported audio filename, if present.

    The stamp is wall-clock from the recording device, with no timezone, so it
    is read as business-local time - the owner records in the business's own
    timezone.
    """
    if not name:
        return None
    match = _FILENAME_STAMP.search(name)
    if not match:
        return None
    try:
        local = datetime(*(int(g) for g in match.groups()), tzinfo=ZoneInfo(tz_name))
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _received_at(message, config: Config | None = None, filename: str | None = None) -> datetime:
    """When the note was actually recorded.

    Three sources, most reliable first: a forwarded message carries the original
    send time; an exported audio file carries its recording time in the filename;
    otherwise it is arriving live. Getting this right is what keeps a relayed
    note in the week and on the weekday it really belongs to - without it the
    report's day-by-day intake becomes fiction.
    """
    origin = getattr(message, "forward_origin", None)
    if origin is not None and getattr(origin, "date", None) is not None:
        return origin.date.astimezone(timezone.utc)

    if config is not None:
        stamped = _timestamp_from_filename(filename, config.report_timezone)
        if stamped is not None:
            return stamped

    return datetime.now(timezone.utc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME)


# Formats the transcription providers accept.
AUDIO_SUFFIXES = {".ogg", ".oga", ".m4a", ".mp3", ".mp4", ".wav", ".webm", ".mpga", ".flac"}


async def _capture_audio(update: Update, context: ContextTypes.DEFAULT_TYPE, media, suffix: str) -> None:
    """Download an audio message, store it, confirm, then transcribe."""
    config: Config = context.application.bot_data["config"]
    message = update.message

    filename = getattr(media, "file_name", None)
    received_at = _received_at(message, config, filename)
    note_id = make_note_id(received_at, media.file_unique_id)
    audio_filename = note_id + suffix

    try:
        telegram_file = await context.bot.get_file(media.file_id)
        await telegram_file.download_to_drive(config.audio_dir / audio_filename)
    except Exception:
        # The owner must never be left unsure whether a note landed.
        log.exception("failed to download audio %s", note_id)
        # He sees SAVE_FAILED; without this nobody else ever learns.
        await alerts.send(
            config,
            f"\u274C Could not download a voice note ({note_id}).\n"
            "The owner was told it failed. Nothing was saved.",
        )
        await message.reply_text(SAVE_FAILED)
        return

    note = VoiceNote(
        note_id=note_id,
        received_at=received_at.isoformat(),
        duration_seconds=getattr(media, "duration", 0) or 0,
        audio_filename=audio_filename,
        telegram_message_id=message.message_id,
        telegram_file_unique_id=media.file_unique_id,
        telegram_chat_id=message.chat_id,
    )

    try:
        append_note(config, note)
    except Exception:
        log.exception("failed to index audio %s", note_id)
        await alerts.send(
            config,
            f"\u274C Downloaded a voice note but could not store it ({note_id}).\n"
            "The audio may be on disk with no database row.",
        )
        await message.reply_text(SAVE_FAILED)
        return

    log.info("saved audio note %s (%ss, from %s)", note_id, note.duration_seconds, filename or "voice")
    await message.reply_text(
        CONFIRMED.format(
            duration=duration(note.duration_seconds),
            count=len(load_notes(config)),
        )
    )

    # Transcribe after confirming, so sending a note always feels instant. A
    # failure here is not reported to the owner - the audio is safely stored and
    # `cli transcribe` retries it. Nothing is lost, and there is nothing they
    # could do about it mid-week.
    await _transcribe_in_background(config, note)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A Telegram voice note - the intended way notes arrive."""
    await _capture_audio(update, context, update.message.voice, VOICE_SUFFIX)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An audio file rather than a Telegram voice note.

    This is how a note recorded elsewhere arrives - a WhatsApp export relayed
    into the chat comes through as an `audio` (or document) attachment, not a
    voice message, and would otherwise be rejected.
    """
    message = update.message
    media = message.audio or message.document
    if media is None:
        return

    suffix = Path(media.file_name or "").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        await message.reply_text(NOT_VOICE)
        return

    await _capture_audio(update, context, media, suffix)


async def _transcribe_in_background(config: Config, note: VoiceNote) -> None:
    """Transcribe a saved note without blocking the event loop."""
    from pomeai.transcription import TranscriptionError, transcribe_note

    try:
        # The provider SDK is synchronous, so keep it off the event loop.
        await asyncio.to_thread(transcribe_note, config, note)
    except TranscriptionError as exc:
        log.warning("transcription failed for %s: %s", note.note_id, exc)
        # The owner already saw a green tick. If this stays silent the note
        # is simply missing from Monday's report and he concludes it did not
        # listen - so this is the alert that matters most.
        await alerts.send(
            config,
            f"\u26A0 Transcription failed for {note.note_id}.\n"
            f"{exc}\n"
            "He was told it saved. It will NOT appear in the report until "
            "`cli transcribe` is run.",
        )
    except Exception:
        log.exception("unexpected transcription error for %s", note.note_id)
        await alerts.send(
            config,
            f"\u26A0 Unexpected transcription error for {note.note_id}. "
            "See the log. He was told it saved.",
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store a typed note.

    Voice is the intended channel - speaking produces rawer, less self-edited
    material than typing. But a typed note beats no note when the owner is
    somewhere he cannot talk, so text is accepted and tagged `source='text'`.
    """
    config: Config = context.application.bot_data["config"]
    message = update.message
    text = (message.text or "").strip()

    if not text:
        return

    received_at = _received_at(message)
    note = VoiceNote(
        note_id=make_note_id(received_at, str(message.message_id)),
        received_at=received_at.isoformat(),
        duration_seconds=0,
        audio_filename="",  # nothing to play back
        telegram_message_id=message.message_id,
        transcript=text,  # already "transcribed" - no ASR step needed
        transcript_provider="telegram:text",
        source="text",
        telegram_chat_id=message.chat_id,
    )

    try:
        append_note(config, note)
    except Exception:
        log.exception("failed to store text note %s", note.note_id)
        await message.reply_text(SAVE_FAILED)
        return

    log.info("saved text note %s (%d chars)", note.note_id, len(text))
    await message.reply_text(
        CONFIRMED_TEXT.format(count=len(load_notes(config)))
    )


async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anything that is neither a voice note nor text - photos, stickers, files."""
    await update.message.reply_text(NOT_VOICE)


async def log_stranger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log messages from any chat that is not the configured owner.

    Setting up the owner's chat id is the fiddliest part of getting started, and
    a silently ignored message looks identical to a broken bot. Logging the id
    turns "message the bot and read the log" into a way to find it.

    Deliberately does not reply: strangers should not learn the bot is live.
    """
    chat = update.effective_chat
    user = update.effective_user
    log.warning(
        "ignored message from chat id %s (%s) - set TELEGRAM_OWNER_CHAT_ID=%s to accept it",
        chat.id,
        user.full_name if user else "unknown",
        chat.id,
    )


async def _on_startup(application) -> None:
    """Tell the operator the bot actually came up.

    A restart that silently failed looks exactly like a restart that worked,
    until a note goes missing days later.
    """
    config = application.bot_data["config"]
    await alerts.send(
        config,
        "\u2705 Bot started.\n"
        f"Accepting notes from chat {config.telegram_owner_chat_id}.\n"
        f"Quiet alert after {config.silence_alert_days} day(s).",
    )


async def _watchdog_job(context) -> None:
    """Daily silence check. Failures here must never stop the bot."""
    from pomeai.watchdog import run_check

    try:
        result = await run_check(context.bot_data["config"])
        log.info("watchdog: %s", result)
    except Exception:
        log.exception("watchdog pass failed")


def build_application(config: Config):
    """Wire up the bot. Raises BotConfigError if it cannot run."""
    if not config.telegram_bot_token:
        raise BotConfigError(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and put "
            "the token in .env"
        )
    if not config.telegram_owner_chat_id:
        raise BotConfigError(
            "TELEGRAM_OWNER_CHAT_ID is not set. This is a single-user system and "
            "the bot refuses to run unlocked - message @userinfobot to find the id."
        )

    try:
        owner_chat_id = int(config.telegram_owner_chat_id)
    except ValueError as exc:
        raise BotConfigError(
            f"TELEGRAM_OWNER_CHAT_ID must be a number, got "
            f"{config.telegram_owner_chat_id!r}"
        ) from exc

    application = ApplicationBuilder().token(config.telegram_bot_token).build()
    application.bot_data["config"] = config

    # Every handler is gated on the owner's chat, so messages from anyone else
    # match nothing and are silently dropped.
    owner_only = filters.Chat(chat_id=owner_chat_id)

    application.add_handler(CommandHandler("start", start, filters=owner_only))
    application.add_handler(MessageHandler(owner_only & filters.VOICE, handle_voice))
    application.add_handler(
        MessageHandler(owner_only & (filters.AUDIO | filters.Document.AUDIO), handle_audio)
    )
    application.add_handler(
        MessageHandler(owner_only & filters.TEXT & ~filters.COMMAND, handle_text)
    )
    application.add_handler(
        MessageHandler(
            owner_only & ~filters.VOICE & ~filters.AUDIO & ~filters.Document.AUDIO
            & ~filters.TEXT & ~filters.COMMAND, handle_other
        )
    )
    # Registered last so it only sees what the owner's handlers did not match.
    application.add_handler(MessageHandler(~owner_only, log_stranger))

    # Handed to the startup hook and the watchdog, which only receive the
    # application/context rather than the config.
    application.bot_data["config"] = config
    application.post_init = _on_startup

    # A dead bot cannot run its own watchdog - see pomeai/watchdog.py. This
    # catches the owner going quiet, not the process dying.
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _watchdog_job,
            interval=timedelta(days=1),
            first=timedelta(minutes=2),
            name="silence-watchdog",
        )
    else:
        log.warning("no job queue available - the silence watchdog is off")

    return application


DISCOVER_REPLY = "✅ Connected. It'll be ready in a few minutes."


async def announce_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Print the sender's chat id, for one-time setup."""
    chat = update.effective_chat
    user = update.effective_user
    name = user.full_name if user else "unknown"

    print("")
    print("=" * 60)
    print(f"  Message from: {name}")
    print(f"  TELEGRAM_OWNER_CHAT_ID={chat.id}")
    print("=" * 60)
    print("  Put that line in .env, stop this (Ctrl-C), then run `bot`.")
    print("")

    await update.message.reply_text(DISCOVER_REPLY)


def run_discovery(config: Config) -> None:
    """Answer any chat and print its id, so the owner's id can be found once.

    `bot` refuses to start without an owner chat id, but the id is only
    discoverable by having the owner message the bot - so setup would deadlock
    without this. Deliberately a separate command: it accepts anyone, and must
    never be what runs during normal operation.
    """
    if not config.telegram_bot_token:
        raise BotConfigError(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and put "
            "the token in .env"
        )

    application = ApplicationBuilder().token(config.telegram_bot_token).build()
    application.bot_data["config"] = config

    # Two groups so both run on the same message. Telegram only delivers an
    # update once, so a voice note sent during discovery would otherwise be
    # consumed and lost - and losing the owner's very first note is the worst
    # possible first impression.
    application.add_handler(MessageHandler(filters.ALL, announce_chat_id), group=0)
    application.add_handler(MessageHandler(filters.VOICE, handle_voice), group=1)
    application.add_handler(
        MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio), group=1
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=1
    )

    print("Discovery mode - ask the owner to send any message to the bot.")
    print("WARNING: this replies to anyone. Stop it as soon as you have the id.")
    print("Press Ctrl-C to stop.")
    application.run_polling()


def run(config: Config) -> None:
    """Start long-polling. Blocks until interrupted.

    Builds first so a misconfiguration surfaces before anything claims the bot
    is listening.
    """
    application = build_application(config)
    log.info("bot starting; locked to chat id %s", config.telegram_owner_chat_id)
    print(f"Listening for voice notes. Audio -> {config.audio_dir}")
    print("Press Ctrl-C to stop.")
    application.run_polling()
