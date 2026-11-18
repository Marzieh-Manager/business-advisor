"""Transcription providers, selected by `TRANSCRIPTION_PROVIDER`."""

from __future__ import annotations

import logging

from pomeai.config import Config
from pomeai.notes import VoiceNote, update_note
from pomeai.transcription.base import (
    DEFAULT_LANGUAGE,
    Transcriber,
    Transcript,
    TranscriptionError,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "Transcriber",
    "Transcript",
    "TranscriptionError",
    "get_transcriber",
    "transcribe_note",
    "PROVIDERS",
]

log = logging.getLogger(__name__)

PROVIDERS = ("whisper", "gemini")


def get_transcriber(config: Config, provider: str | None = None) -> Transcriber:
    """Build a transcriber, defaulting to the configured provider.

    Adding a provider means adding a branch here and a module beside this one -
    nothing else in the system needs to know which one is in use. Passing
    `provider` explicitly is how `cli compare` runs several side by side.
    """
    name = (provider or config.transcription_provider).lower()

    if name == "whisper":
        from pomeai.transcription.whisper import WhisperTranscriber

        return WhisperTranscriber(
            api_key=config.openai_api_key or "",
            model=config.whisper_model,
            base_url=config.openai_base_url,
        )

    if name == "gemini":
        from pomeai.transcription.gemini import GeminiTranscriber

        return GeminiTranscriber(
            api_key=config.gemini_api_key or "",
            model=config.gemini_model,
        )

    raise TranscriptionError(
        f"unknown transcription provider {name!r}; available: {', '.join(PROVIDERS)}"
    )


def transcribe_note(
    config: Config,
    note: VoiceNote,
    transcriber: Transcriber | None = None,
) -> VoiceNote:
    """Transcribe one note and write the result back to the index.

    Returns the updated note. Raises TranscriptionError on failure, leaving the
    note untranscribed so it stays visible as retryable rather than silently
    becoming a note with no text.
    """
    engine = transcriber or get_transcriber(config)
    result = engine.transcribe(note.audio_path(config))

    note.transcript = result.text
    note.transcript_provider = f"{result.provider}:{result.model}"
    update_note(config, note)

    log.info("transcribed %s (%d chars)", note.note_id, len(result.text))
    return note
