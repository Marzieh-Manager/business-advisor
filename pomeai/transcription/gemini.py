"""Google Gemini transcription provider.

Unlike Whisper, Gemini is a general model being *asked* to transcribe rather
than a dedicated ASR system. That cuts both ways: it can use context to get
domain terms right, but it can also drift into summarising or commenting
instead of transcribing. The prompt below is written to shut that down, and
`_strip_preamble` catches the common cases where it leaks through anyway.

Which of Whisper and Gemini is better on the owner's audio is an empirical
question - use `cli compare` on real audio to answer it.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
from pathlib import Path

from pomeai.transcription.base import (
    DEFAULT_LANGUAGE,
    Transcript,
    TranscriptionError,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash"

# Total request size is capped at 20MB, and base64 inflates bytes by ~33%.
# 14MB of raw audio is roughly 19MB encoded - comfortably inside the cap, and
# far larger than any voice note.
MAX_INLINE_BYTES = 14_000_000

# Telegram sends OGG/Opus, which `mimetypes` does not always know.
MIME_OVERRIDES = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/aac",
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
}

TRANSCRIBE_PROMPT = """Transcribe this audio verbatim.

Rules:
- Write exactly what was said. Do not summarise, correct, or tidy it up.
- Output the transcript only - no preamble, heading, explanation or note.
- Transcribe in whatever language the speaker uses. The owner usually speaks
  English, sometimes Persian, and mixes English business terms into Persian.
  Always write English terms in English, never transliterated.
- Mark anything unintelligible as [unclear] in place.

This is a voice note from the owner of Harbourline Coffee Co., a Sydney
wholesale coffee roaster. Terms you may hear: wholesale, cafe account, green
beans, roast schedule, single origin, blend, Shopify, order, stock, invoice,
purchase order, Australia Post, La Marzocco, barista training, cold brew,
subscription, B2B, loyal customer."""

# Models like to open with "Here is the transcript:" no matter what they are told.
_PREAMBLE = re.compile(
    r"^\s*(?:here(?:'s| is)\s+(?:the\s+)?)?"
    r"(?:transcript|transcription|رونویسی|متن|خروجی)\b[^\n:]*:\s*",
    re.IGNORECASE,
)


def _mime_type(audio_path: Path) -> str:
    suffix = audio_path.suffix.lower()
    if suffix in MIME_OVERRIDES:
        return MIME_OVERRIDES[suffix]
    guessed, _ = mimetypes.guess_type(audio_path.name)
    if guessed and guessed.startswith("audio/"):
        return guessed
    raise TranscriptionError(f"unsupported audio format for Gemini: {suffix or audio_path.name}")


def _strip_preamble(text: str) -> str:
    """Remove a leading 'Here is the transcript:' style line, and code fences."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            # Drop a language tag on the opening fence.
            text = text.split("\n", 1)[1] if "\n" in text and " " not in text.split("\n", 1)[0] else text
    return _PREAMBLE.sub("", text.strip()).strip()


class GeminiTranscriber:
    """Transcribes via the Gemini Interactions API."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise TranscriptionError("GEMINI_API_KEY is not set - transcription cannot run.")
        # Imported lazily so the CLI works without google-genai installed.
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def transcribe(self, audio_path: Path, language: str = DEFAULT_LANGUAGE) -> Transcript:
        if not audio_path.exists():
            raise TranscriptionError(f"audio file not found: {audio_path}")

        mime_type = _mime_type(audio_path)
        raw = audio_path.read_bytes()

        if len(raw) > MAX_INLINE_BYTES:
            raise TranscriptionError(
                f"{audio_path.name} is {len(raw) / 1_000_000:.1f}MB - too large to send "
                f"inline (limit ~{MAX_INLINE_BYTES // 1_000_000}MB). Split the recording, "
                "or use the whisper provider for this file."
            )

        log.info("transcribing %s with %s", audio_path.name, self._model)
        try:
            # Sent inline rather than via the Files API: audio uploaded there is
            # rejected with 403 for some key types, and a voice note is small
            # enough that inline is simpler anyway - nothing is left sitting on
            # Google's servers afterwards.
            interaction = self._client.interactions.create(
                model=self._model,
                input=[
                    {"type": "text", "text": TRANSCRIBE_PROMPT},
                    {
                        "type": "audio",
                        "data": base64.b64encode(raw).decode(),
                        "mime_type": mime_type,
                    },
                ],
            )
        except Exception as exc:
            raise TranscriptionError(f"{self._model} failed on {audio_path.name}: {exc}") from exc

        text = _strip_preamble(interaction.output_text or "")
        if not text:
            raise TranscriptionError(f"{audio_path.name} produced an empty transcript")

        return Transcript(text=text, provider=self.name, model=self._model)
