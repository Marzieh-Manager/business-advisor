"""The transcription interface.

ASR accuracy is a first-class risk for this product - a weak transcript
makes every downstream analysis weak. The provider therefore sits behind this
small interface so it can be swapped or A/B tested (Deepgram, ElevenLabs Scribe)
without touching the bot, storage or the advisor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The owner records in English. Passing the language explicitly measurably
# improves Whisper's output over letting it auto-detect. Gemini ignores this and
# follows whatever the speaker actually uses, which is the safer behaviour if he
# ever slips back into Persian mid-note.
DEFAULT_LANGUAGE = "en"


class TranscriptionError(RuntimeError):
    """Transcription failed. The caller decides whether to retry."""


@dataclass(frozen=True)
class Transcript:
    """The result of transcribing one audio file."""

    text: str
    provider: str  # e.g. "whisper"
    model: str  # e.g. "whisper-1"


class Transcriber(Protocol):
    """What every transcription provider must offer."""

    name: str

    def transcribe(self, audio_path: Path, language: str = DEFAULT_LANGUAGE) -> Transcript:
        """Return the transcript, or raise TranscriptionError."""
        ...
