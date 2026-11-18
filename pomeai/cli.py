"""Command-line entrypoint.

Milestone 0 ships a single command, `check`, which is the milestone's
done-condition made runnable: config, business context and report template all
load cleanly.
"""

from __future__ import annotations

import logging
import sys

import typer
from jinja2 import TemplateNotFound

from pomeai.config import Config
from pomeai.context import (
    BusinessContextError,
    count_todos,
    load_business_context,
    load_sector_context,
)
from pomeai.notes import load_notes
from pomeai.report import build_jinja_env
from pomeai.formatting import duration

app = typer.Typer(
    help="POMEAI Advisor - weekly AI chief of staff for a business owner.",
    no_args_is_help=True,
)

OK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252, which cannot encode every character.

    Transcripts carry names and punctuation cp1252 has no mapping for, so make
    stdout UTF-8 before anything tries to print them.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@app.callback()
def main() -> None:
    """Keep subcommand routing even while only one command exists.

    Without a callback Typer collapses a single-command app into a bare
    command, so `cli check` would fail until Milestone 1 adds a second one.
    """


@app.command()
def check() -> None:
    """Verify that config, business context and report template all load."""
    _force_utf8_stdout()
    failures = 0

    typer.echo("POMEAI Advisor - setup check")
    typer.echo("=" * 60)

    # --- Config ---
    try:
        config = Config.load()
    except Exception as exc:  # surfacing the message matters more than the type
        typer.echo(f"{FAIL} config: {exc}")
        raise typer.Exit(code=1)

    typer.echo(f"{OK} config loaded")
    typer.echo(f"       project root:  {config.base_dir}")
    typer.echo(f"       data dir:      {config.data_dir}")
    typer.echo(f"       timezone:      {config.report_timezone}")
    typer.echo(f"       week starts:   {config.week_start}")
    if config.transcription_provider.lower() == "gemini":
        typer.echo(f"       transcription: gemini ({config.gemini_model})")
    else:
        route = f" via {config.openai_base_url}" if config.openai_base_url else ""
        typer.echo(f"       transcription: whisper ({config.whisper_model}){route}")
    typer.echo(f"       advisor model: {config.anthropic_model}")

    # --- Business context ---
    typer.echo("")
    try:
        context_text = load_business_context(config)
    except BusinessContextError as exc:
        typer.echo(f"{FAIL} business context: {exc}")
        failures += 1
    else:
        todos = count_todos(context_text)
        typer.echo(
            f"{OK} business context loaded "
            f"({len(context_text):,} chars, {config.business_context_file.name})"
        )
        if todos:
            typer.echo(
                f"{WARN} {todos} unfilled [TODO] marker(s) remain - "
                "the report is only as specific as this file"
            )

    # The sector lens is optional - a missing one degrades advice quality but
    # must never block a report, so this reports rather than fails.
    sector = load_sector_context(config)
    if sector:
        typer.echo(f"{OK} sector context loaded ({len(sector):,} chars, sector.md)")
        verifies = sector.count("[VERIFY:")
        if verifies:
            typer.echo(f"       {verifies} [VERIFY] marker(s) - raise these as questions, never as facts")
    else:
        typer.echo(f"{WARN} no sector context - advice will be less category-aware")

    # --- Report template ---
    typer.echo("")
    env = build_jinja_env(config)
    try:
        env.get_template(config.report_template_name)
    except TemplateNotFound:
        typer.echo(f"{FAIL} report template not found: {config.report_template_path}")
        failures += 1
    else:
        typer.echo(f"{OK} report template loaded ({config.report_template_name})")

    # --- Environment keys ---
    # Absent keys are reported, never fatal: Milestone 0 requires no secrets.
    # The two Telegram values sit on adjacent lines and are easy to swap. A bot
    # token has the shape `<digits>:<secret>`; a chat id is a plain number.
    if config.telegram_owner_chat_id and ":" in config.telegram_owner_chat_id:
        typer.echo("")
        typer.echo(
            f"{FAIL} TELEGRAM_OWNER_CHAT_ID looks like a bot token, not a chat id."
        )
        typer.echo("       It should be a plain number. Did the two lines get swapped?")
        failures += 1
    if config.telegram_bot_token and ":" not in config.telegram_bot_token:
        typer.echo("")
        typer.echo(f"{FAIL} TELEGRAM_BOT_TOKEN does not look like a bot token.")
        typer.echo("       Expected `<digits>:<secret>` from @BotFather.")
        failures += 1

    # A gateway setup fails in two quiet ways: a bare model id gets rejected at
    # call time, and the domain prompt is dropped without any error at all.
    if config.openai_base_url:
        typer.echo("")
        if "/" not in config.whisper_model:
            typer.echo(
                f"{WARN} WHISPER_MODEL={config.whisper_model!r} has no namespace. "
                "Gateways usually need e.g. 'openai/whisper-large-v3'."
            )
        if "openrouter" in config.openai_base_url.lower():
            typer.echo(
                f"{WARN} OpenRouter ignores the transcription `prompt`, so the business "
                "domain vocabulary is dropped."
            )
            typer.echo(
                "       Check that supplier and product names survive transcription; "
                "if not, use a direct OpenAI key."
            )

    typer.echo("")
    # Inbound and outbound are different permissions. Being alerted must not
    # imply being able to send notes into the owner's week.
    if config.telegram_alert_chat_id:
        if config.telegram_alert_chat_id == config.telegram_owner_chat_id:
            typer.echo(
                f"{WARN} TELEGRAM_ALERT_CHAT_ID is the same as the owner - "
                "failure alerts would go to him, not to you."
            )
        elif not config.telegram_alert_chat_id.lstrip("-").isdigit():
            typer.echo(
                f"{FAIL} TELEGRAM_ALERT_CHAT_ID should be a number, got "
                f"{config.telegram_alert_chat_id!r}"
            )
        else:
            typer.echo(
                f"{OK} alerts -> chat {config.telegram_alert_chat_id} "
                f"(quiet after {config.silence_alert_days} day(s))"
            )
    else:
        typer.echo(
            f"{WARN} TELEGRAM_ALERT_CHAT_ID not set - a failed transcription "
            "would be silent, and the note would vanish from the report."
        )

    typer.echo("Environment keys by milestone:")
    for milestone, missing in config.missing_keys.items():
        if missing:
            typer.echo(f"  {WARN} {milestone}: missing {', '.join(missing)}")
        else:
            typer.echo(f"  {OK} {milestone}: configured")

    typer.echo("")
    typer.echo("=" * 60)
    if failures:
        typer.echo(f"{FAIL} {failures} check(s) failed.")
        raise typer.Exit(code=1)
    typer.echo(f"{OK} Milestone 0 checks passed.")


def _run_bot(discovery: bool) -> None:
    _force_utf8_stdout()
    # Imported here so `check` and `notes` still work when python-telegram-bot
    # or the token are not available.
    from pomeai.bot import BotConfigError, run, run_discovery

    config = Config.load()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # `logs/` existed but nothing ever wrote to it, so closing the terminal
    # destroyed the only record of a failure. The bot runs unattended for
    # days at a time - it needs a trail that outlives the console.
    from logging.handlers import RotatingFileHandler

    config.logs_dir.mkdir(parents=True, exist_ok=True)
    file_log = RotatingFileHandler(
        config.logs_dir / "bot.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8",
    )
    file_log.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(file_log)

    try:
        (run_discovery if discovery else run)(config)
    except BotConfigError as exc:
        typer.echo(f"{FAIL} {exc}")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def bot() -> None:
    """Run the Telegram bot that receives voice notes. Blocks until Ctrl-C."""
    _run_bot(discovery=False)


@app.command()
def discover() -> None:
    """One-time setup: find the owner's chat id by having them message the bot.

    `bot` refuses to start without TELEGRAM_OWNER_CHAT_ID, and that id can only
    be learned from an incoming message - so first-time setup needs this.
    """
    _run_bot(discovery=True)


def _resolve_week(config: Config, week: str | None):
    """Turn a `--week` value into a Week, or None for 'everything'."""
    from pomeai.weeks import current_week, last_week, week_offset

    if week is None:
        return None
    value = week.strip().lower()
    if value == "current":
        return current_week(config)
    if value == "last":
        return last_week(config)
    try:
        # A negative number counts back from this week: -2 is two weeks ago.
        return week_offset(config, int(value))
    except ValueError:
        raise typer.BadParameter(
            f"--week must be 'current', 'last', or a number like -2; got {week!r}"
        )


WEEK_OPTION = typer.Option(
    None, "--week", help="Which week: 'current', 'last', or an offset like -2."
)


@app.command()
def notes(
    limit: int = typer.Option(20, help="How many of the most recent notes to show."),
    week: str = WEEK_OPTION,
) -> None:
    """List captured voice notes, newest last."""
    _force_utf8_stdout()
    config = Config.load()

    selected = _resolve_week(config, week)
    if selected is None:
        all_notes = load_notes(config)
        scope = f"{len(all_notes)} note(s) captured"
    else:
        all_notes = load_notes(config, start=selected.start, end=selected.end)
        scope = f"{len(all_notes)} note(s) in {selected.label}"

    if not all_notes:
        if selected is not None:
            typer.echo(f"No voice notes in {selected.label}.")
        else:
            typer.echo("No voice notes captured yet. Run `bot` and send one from Telegram.")
        return

    shown = all_notes[-limit:]
    typer.echo(f"{scope}; showing last {len(shown)}.")
    typer.echo(f"Week starts {config.week_start}; weekdays in {config.report_timezone}.")
    typer.echo("-" * 72)

    for note in shown:
        local = note.local_time(config.report_timezone)
        transcript = note.transcript
        # Transcripts arrive at Milestone 2; until then say so rather than
        # printing a blank line that looks like a failure.
        preview = (
            (transcript[:60].replace("\n", " ") + ("..." if len(transcript) > 60 else ""))
            if transcript
            else "(not transcribed yet)"
        )
        typer.echo(
            f"{local:%Y-%m-%d %H:%M}  {note.weekday(config.report_timezone):<9} "
            f"{('text' if note.is_text else duration(note.duration_seconds)):>5}  {preview}"
        )

    typer.echo("-" * 72)
    typer.echo(f"Audio: {config.audio_dir}")
    pending = sum(1 for n in all_notes if not n.transcript and not n.is_text)
    if pending:
        typer.echo(f"{pending} note(s) not transcribed - run `transcribe` to fill them in.")


@app.command("import-audio")
def import_audio(
    path: str = typer.Argument(..., help="An audio file, or a folder of them."),
    received_at: str = typer.Option(
        None,
        "--received-at",
        help="When the note was recorded, ISO 8601. Defaults to the file's modified time.",
    ),
) -> None:
    """Import existing audio files as voice notes.

    Capture is Telegram, but the owner's first samples usually arrive some other
    way (WhatsApp, email, a folder of recordings). This gets real audio
    into the system so transcription accuracy and the advisor prompt can be
    tuned against it, rather than against invented notes.
    """
    _force_utf8_stdout()
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from pomeai.notes import VoiceNote, append_note, make_note_id

    # Formats the transcription API accepts.
    audio_suffixes = {".ogg", ".oga", ".m4a", ".mp3", ".mp4", ".wav", ".webm", ".mpga", ".flac"}

    config = Config.load()
    source = Path(path).expanduser()
    if not source.exists():
        typer.echo(f"{FAIL} not found: {source}")
        raise typer.Exit(code=1)

    if source.is_dir():
        files = sorted(p for p in source.iterdir() if p.suffix.lower() in audio_suffixes)
        if not files:
            typer.echo(f"{FAIL} no audio files in {source}")
            raise typer.Exit(code=1)
    else:
        files = [source]

    if received_at and len(files) > 1:
        typer.echo(f"{FAIL} --received-at applies to a single file, but {len(files)} were given.")
        raise typer.Exit(code=1)

    existing = {n.audio_filename for n in load_notes(config)}
    imported = skipped = 0

    for audio_file in files:
        if received_at:
            try:
                moment = datetime.fromisoformat(received_at)
            except ValueError:
                raise typer.BadParameter(f"--received-at is not ISO 8601: {received_at!r}")
            if moment.tzinfo is None:
                # A bare timestamp means the business's local time, not UTC.
                moment = moment.replace(tzinfo=ZoneInfo(config.report_timezone))
        else:
            moment = datetime.fromtimestamp(audio_file.stat().st_mtime, tz=timezone.utc)

        note_id = make_note_id(moment, audio_file.stem)
        stored_name = note_id + audio_file.suffix.lower()

        if stored_name in existing:
            typer.echo(f"  {WARN} already imported, skipping: {audio_file.name}")
            skipped += 1
            continue

        # Copy rather than move - never consume the only copy of the owner's audio.
        shutil.copy2(audio_file, config.audio_dir / stored_name)
        append_note(
            config,
            VoiceNote(
                note_id=note_id,
                received_at=moment.astimezone(timezone.utc).isoformat(),
                duration_seconds=0,  # unknown until transcription; not worth probing here
                audio_filename=stored_name,
            ),
        )
        local = moment.astimezone(ZoneInfo(config.report_timezone))
        typer.echo(f"  {OK} {audio_file.name} -> {local:%Y-%m-%d %H:%M} ({stored_name})")
        imported += 1

    typer.echo(f"\n{imported} imported, {skipped} skipped.")
    if imported:
        typer.echo("Next: `transcribe` to turn them into text, then `transcripts` to read them.")


@app.command()
def transcribe(
    force: bool = typer.Option(
        False, "--force", help="Re-transcribe notes that already have a transcript."
    ),
) -> None:
    """Transcribe captured voice notes that have no transcript yet."""
    _force_utf8_stdout()
    from pomeai.transcription import TranscriptionError, get_transcriber, transcribe_note

    config = Config.load()
    logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO), format="%(message)s")

    all_notes = load_notes(config)
    # Text notes arrive already 'transcribed' - never send them to an ASR API.
    pending = [n for n in all_notes if not n.transcript and not n.is_text]
    targets = [n for n in all_notes if not n.is_text] if force else pending

    if not targets:
        typer.echo(
            "Nothing to transcribe."
            if all_notes
            else "No voice notes captured yet. Run `bot` and send one from Telegram."
        )
        return

    try:
        engine = get_transcriber(config)
    except TranscriptionError as exc:
        typer.echo(f"{FAIL} {exc}")
        raise typer.Exit(code=1)

    typer.echo(f"Transcribing {len(targets)} note(s) with {config.transcription_provider}...")
    done = failed = 0
    for note in targets:
        try:
            updated = transcribe_note(config, note, engine)
        except TranscriptionError as exc:
            # Keep going: one bad recording must not block the rest of the week.
            failed += 1
            typer.echo(f"  {FAIL} {note.note_id}: {exc}")
        else:
            done += 1
            typer.echo(f"  {OK} {note.note_id} ({len(updated.transcript)} chars)")

    typer.echo(f"\n{done} transcribed, {failed} failed.")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def report(
    week: str = typer.Option(
        "last", "--week", help="Which week: 'current', 'last', or an offset like -2."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Assemble and log the prompt without calling the API."
    ),
    no_follow_up: bool = typer.Option(
        False, "--no-follow-up", help="Skip passing the previous report in."
    ),
) -> None:
    """Generate the weekly report for a week, on demand.

    Defaults to last week - the week a Monday report covers.
    """
    _force_utf8_stdout()
    from pomeai import report as report_module
    from pomeai import decisions as decisions_module
    from pomeai.advisor import AdvisorError, build_prompt, generate, log_prompt

    config = Config.load()
    logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO), format="%(message)s")

    selected = _resolve_week(config, week)
    notes = load_notes(config, start=selected.start, end=selected.end)

    typer.echo(f"Week: {selected.label}")
    typer.echo(f"Notes: {len(notes)}")

    if not notes:
        typer.echo(f"{FAIL} No voice notes in this week - nothing to analyse.")
        raise typer.Exit(code=1)

    untranscribed = [n for n in notes if not n.transcript]
    if untranscribed:
        # The advisor reads transcripts, so an untranscribed note is invisible
        # to it - say so rather than silently analysing a partial week.
        typer.echo(
            f"{WARN} {len(untranscribed)} note(s) have no transcript and will be "
            "ignored. Run `transcribe` first."
        )

    previous_path = None if no_follow_up else report_module.find_previous_report(config, selected)
    previous_text = previous_path.read_text(encoding="utf-8") if previous_path else None
    typer.echo(f"Follow-up: {previous_path.name if previous_path else 'none (first report)'}")

    # The advisor's memory: what it has already told him, and how long each
    # thing has been open. Counted here rather than asked of the model.
    history = decisions_module.history_block(config, str(selected.local_start))
    if history:
        open_threads = [
            t for t in decisions_module.threads(config, as_of_week=str(selected.local_start))
            if t.is_open and t.first_week < str(selected.local_start)
        ]
        oldest = max(
            (t.weeks_running(str(selected.local_start)) for t in open_threads),
            default=0,
        )
        typer.echo(
            f"Memory: {len(open_threads)} open topic(s), oldest running {oldest} week(s)"
        )
    else:
        typer.echo("Memory: empty - no decisions tracked yet")

    if dry_run:
        system, user = build_prompt(config, selected, notes, previous_text, history)
        path = log_prompt(config, selected, system, user)
        typer.echo("")
        typer.echo(f"{OK} Prompt assembled, no API call made.")
        typer.echo(f"       {path}")
        typer.echo(f"       {len(system) + len(user):,} chars")
        return

    typer.echo(f"Model: {config.anthropic_model} (effort {config.advisor_effort})")
    typer.echo("Generating - this takes a while at high effort...")

    try:
        result = generate(config, selected, notes, previous_text, history=history)
    except AdvisorError as exc:
        typer.echo(f"{FAIL} {exc}")
        raise typer.Exit(code=1)

    path = report_module.save(config, selected, notes, result.sections)

    # Written after the report exists, so a render failure cannot leave the
    # store claiming he was told something he was never actually sent.
    resolved = decisions_module.apply_follow_up(
        config, result.sections, str(selected.local_start)
    )
    issued = decisions_module.record(
        config,
        decisions_module.from_sections(
            result.sections, str(selected.local_start), path.name
        ),
    )

    typer.echo("")
    typer.echo(f"{OK} Report written")
    typer.echo(f"       {path}")
    typer.echo(f"       prompt: {result.prompt_path}")
    typer.echo(f"       tracked: {issued} new decision(s), {resolved} followed up")
    if result.input_tokens is not None:
        typer.echo(f"       tokens: {result.input_tokens:,} in / {result.output_tokens:,} out")


@app.command()
def compare(
    path: str = typer.Argument(..., help="Audio file to transcribe with each provider."),
    providers: str = typer.Option(
        "whisper,gemini", "--providers", help="Comma-separated providers to run."
    ),
) -> None:
    """Transcribe one file with several providers, to compare accuracy.

    Which provider handles the owner's audio best is an empirical question -
    accents, code-switching into English, and the domain vocabulary all matter.
    This runs them on the same audio so the answer comes from evidence rather
    than from anyone's opinion. Nothing is written to the database.
    """
    _force_utf8_stdout()
    import time
    from pathlib import Path

    from pomeai.transcription import TranscriptionError, get_transcriber

    config = Config.load()
    audio = Path(path).expanduser()
    if not audio.exists():
        typer.echo(f"{FAIL} not found: {audio}")
        raise typer.Exit(code=1)

    wanted = [p.strip().lower() for p in providers.split(",") if p.strip()]
    typer.echo(f"Comparing {len(wanted)} provider(s) on {audio.name}")
    typer.echo("=" * 72)

    results: list[tuple[str, float, str]] = []
    for name in wanted:
        typer.echo("")
        try:
            engine = get_transcriber(config, provider=name)
        except TranscriptionError as exc:
            typer.echo(f"{WARN} {name}: skipped - {exc}")
            continue

        started = time.monotonic()
        try:
            result = engine.transcribe(audio)
        except TranscriptionError as exc:
            typer.echo(f"{FAIL} {name}: {exc}")
            continue
        elapsed = time.monotonic() - started

        results.append((f"{result.provider}:{result.model}", elapsed, result.text))
        typer.echo(f"{OK} {result.provider} ({result.model}) - {elapsed:.1f}s, {len(result.text)} chars")
        typer.echo("-" * 72)
        typer.echo(result.text)

    typer.echo("")
    typer.echo("=" * 72)
    if len(results) < 2:
        typer.echo("Need at least two providers to compare. Check the API keys in .env.")
        return

    typer.echo("Summary:")
    for label, elapsed, text in results:
        typer.echo(f"  {label:<28} {elapsed:>6.1f}s  {len(text):>6} chars")
    typer.echo("")
    typer.echo(
        "Read both against the audio yourself. Length alone proves nothing - what "
        "matters is whether names, numbers and English terms came through right."
    )


@app.command()
def transcripts(
    week: str = typer.Option(
        "last", "--week", help="Which week: 'current', 'last', an offset like -2, or 'all'."
    ),
) -> None:
    """Dump a week's transcripts in full, for manually checking accuracy.

    ASR is imperfect and can make or break this product, so the raw
    transcript must always be readable end to end - never truncated.

    Defaults to last week: the week a start-of-week report covers.
    """
    _force_utf8_stdout()
    config = Config.load()

    if week.strip().lower() == "all":
        selected = None
        recent = load_notes(config)
        scope = "all time"
    else:
        selected = _resolve_week(config, week)
        recent = load_notes(config, start=selected.start, end=selected.end)
        scope = selected.label

    if not recent:
        typer.echo(f"No voice notes in {scope}.")
        return

    typer.echo(f"{len(recent)} note(s) in {scope}, {config.report_timezone}.")
    typer.echo("=" * 72)

    for note in recent:
        local = note.local_time(config.report_timezone)
        typer.echo("")
        typer.echo(
            f"{note.weekday(config.report_timezone)}  {local:%Y-%m-%d %H:%M}  "
            f"{duration(note.duration_seconds)}  [{note.note_id}]"
        )
        typer.echo(f"source: {note.source} | provider: {note.transcript_provider or '-'}")
        typer.echo("-" * 72)
        typer.echo(note.transcript or "(not transcribed yet)")

    typer.echo("")
    typer.echo("=" * 72)
    typer.echo(f"Audio for playback: {config.audio_dir}")


@app.command()
def topics() -> None:
    """Every thread the advisor is tracking, longest-running first.

    This is the archive: rather than hunting through weekly reports, open a
    subject and see the whole history of what was recommended and what happened.
    """
    _force_utf8_stdout()
    from pomeai import decisions as decisions_module
    from pomeai.weeks import current_week

    config = Config.load()
    today = current_week(config)
    as_of = str(today.local_start)
    threads = decisions_module.threads(config)

    if not threads:
        typer.echo("No decisions tracked yet - generate a report first.")
        return

    open_threads = [t for t in threads if t.is_open]
    closed = [t for t in threads if not t.is_open]

    typer.echo(f"{len(threads)} topic(s): {len(open_threads)} open, {len(closed)} closed")
    typer.echo("")

    for thread in sorted(open_threads, key=lambda t: -t.weeks_running(as_of)):
        weeks = thread.weeks_running(as_of)
        raised = thread.times_raised()
        flag = "  <-- stalled" if weeks >= 3 else ""
        typer.echo(
            f"  OPEN    {thread.topic:24} in its {decisions_module.ordinal(weeks)} week, "
            f"raised in {raised}{flag}"
        )
    for thread in closed:
        typer.echo(f"  closed  {thread.topic:24} {thread.decisions[-1].status}")

    typer.echo("")
    typer.echo("Open one with:  topic <name>")


@app.command()
def topic(
    name: str = typer.Argument(..., help="Topic slug, e.g. warehouse-hire."),
) -> None:
    """The full history of one subject - every recommendation and its outcome."""
    _force_utf8_stdout()
    from pomeai import decisions as decisions_module
    from pomeai.weeks import current_week

    config = Config.load()
    slug = decisions_module.slugify(name)
    tracked = decisions_module.load(config, topic=slug)

    if not tracked:
        known = [t.topic for t in decisions_module.threads(config)]
        typer.echo(f"{FAIL} No topic {slug!r}.")
        if known:
            typer.echo(f"       Known: {', '.join(known)}")
        raise typer.Exit(code=1)

    as_of = str(current_week(config).local_start)
    thread = next(t for t in decisions_module.threads(config) if t.topic == slug)

    typer.echo(f"{slug}")
    typer.echo(
        f"  first raised {thread.first_week}, in its "
        f"{decisions_module.ordinal(thread.weeks_running(as_of))} week, "
        f"{'still open' if thread.is_open else 'closed'}"
    )
    typer.echo("")

    for decision in tracked:
        mark = {"done": "[x]", "partial": "[~]", "not_started": "[ ]",
                "dropped": "[-]", "open": "[ ]"}.get(decision.status, "[?]")
        typer.echo(f"  {decision.week_start}  {mark} {decision.subject}")
        if decision.detail:
            typer.echo(f"                  {decision.detail}")
        if decision.status_note:
            typer.echo(f"                  -> {decision.status}: {decision.status_note}")
        typer.echo("")


@app.command()
def status() -> None:
    """Is this thing working? Last note, backlog, open topics, alert wiring.

    A dead bot cannot report its own death, so this is the only cover for a
    stopped process until the bot runs on a supervised server.
    """
    _force_utf8_stdout()
    from datetime import datetime, timezone

    from pomeai import decisions as decisions_module
    from pomeai.weeks import current_week

    config = Config.load()
    notes = load_notes(config)
    now = datetime.now(timezone.utc)

    typer.echo("POMEAI Advisor - status")
    typer.echo("=" * 60)

    if not notes:
        typer.echo(f"{WARN} No voice notes have ever been received.")
    else:
        newest = notes[-1]
        try:
            last_at = datetime.fromisoformat(newest.received_at).astimezone(timezone.utc)
            quiet = (now - last_at).days
            hours = int((now - last_at).total_seconds() // 3600)
            ago = f"{quiet} day(s) ago" if quiet else f"{hours} hour(s) ago"
            mark = WARN if quiet >= config.silence_alert_days else OK
            typer.echo(f"{mark} Last note {ago} ({last_at:%a %d %b %H:%M UTC})")
        except (ValueError, TypeError):
            typer.echo(f"{WARN} Last note has an unreadable timestamp: {newest.received_at!r}")

        week = current_week(config)
        this_week = [n for n in notes if week.start <= _as_utc(n.received_at) < week.end]
        typer.echo(f"       {len(notes)} note(s) total, {len(this_week)} in {week.label}")

        # A note with no transcript is invisible to the advisor - it is saved,
        # the owner was told so, and the report will simply not mention it.
        pending = [n for n in notes if not n.transcript and not n.is_text]
        if pending:
            typer.echo(f"{WARN} {len(pending)} note(s) NOT transcribed - they will be "
                       "missing from the report. Run `transcribe`.")
        else:
            typer.echo(f"{OK} Every note is transcribed")

    threads = decisions_module.threads(config)
    open_threads = [t for t in threads if t.is_open]
    if threads:
        as_of = str(current_week(config).local_start)
        oldest = max((t.weeks_running(as_of) for t in open_threads), default=0)
        typer.echo(f"{OK} {len(open_threads)} open topic(s) of {len(threads)}, "
                   f"oldest in its {decisions_module.ordinal(oldest)} week")
    else:
        typer.echo(f"       No decisions tracked yet")

    typer.echo("")
    if config.telegram_alert_chat_id:
        typer.echo(f"{OK} Alerts go to chat {config.telegram_alert_chat_id}")
    else:
        typer.echo(f"{WARN} TELEGRAM_ALERT_CHAT_ID is not set - failures will be silent")
    typer.echo(f"       Owner (may send notes): {config.telegram_owner_chat_id or 'NOT SET'}")


def _as_utc(iso: str):
    """Parse a stored timestamp to UTC, tolerating a bad row."""
    from datetime import datetime, timezone

    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    app()
