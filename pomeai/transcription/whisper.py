"""Whisper transcription, via OpenAI or any OpenAI-compatible gateway.

Set `OPENAI_BASE_URL` to point at a gateway such as OpenRouter
(`https://openrouter.ai/api/v1`), which exposes the same
`/audio/transcriptions` endpoint and works with the official OpenAI SDK.

Caveat when routing through a gateway: OpenRouter accepts the `prompt`
parameter but **ignores it** on multipart uploads, so the domain vocabulary
below is silently dropped. That matters here - it is what keeps supplier, product
and trade names from being mangled - so prefer a direct OpenAI key if transcript
accuracy on those terms turns out to be weak. `cli check` warns about this.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pomeai.transcription.base import (
    DEFAULT_LANGUAGE,
    Transcript,
    TranscriptionError,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "whisper-1"

# Gateways namespace their models; a bare "whisper-1" is rejected there.
GATEWAY_MODEL_HINT = "openai/whisper-1"

# Nudges Whisper toward the spellings this business actually uses. Without it,
# ASR tends to mangle the product, supplier and scheme names
# that appear constantly in these notes.
DOMAIN_PROMPT = (
    "A voice note from the owner of Harbourline Coffee Co., a Sydney wholesale coffee roaster. "
    "Common terms: wholesale, cafe account, green beans, roast schedule, single origin, blend, "
    "Shopify, order, stock, warehouse, courier, Australia Post, invoice, purchase order, "
    "La Marzocco, barista training, cold brew, subscription, B2B, loyal customer."
)


class WhisperTranscriber:
    """Transcribes via the OpenAI audio transcriptions API."""

    name = "whisper"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
    ) -> None:
        if not api_key:
            raise TranscriptionError(
                "No API key for Whisper - set OPENAI_API_KEY (or an OpenRouter key "
                "plus OPENAI_BASE_URL)."
            )
        # Imported lazily so the CLI works without the openai package installed.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self._model = model
        self._base_url = base_url

    def transcribe(self, audio_path: Path, language: str = DEFAULT_LANGUAGE) -> Transcript:
        if not audio_path.exists():
            raise TranscriptionError(f"audio file not found: {audio_path}")

        log.info("transcribing %s with %s", audio_path.name, self._model)
        try:
            with audio_path.open("rb") as audio:
                response = self._client.audio.transcriptions.create(
                    model=self._model,
                    file=audio,
                    language=language,
                    prompt=DOMAIN_PROMPT,
                )
        except Exception as exc:
            hint = ""
            if self._base_url and "/" not in self._model:
                hint = (
                    f" (a gateway usually needs a namespaced model id, "
                    f"e.g. WHISPER_MODEL={GATEWAY_MODEL_HINT})"
                )
            raise TranscriptionError(
                f"{self._model} failed on {audio_path.name}: {exc}{hint}"
            ) from exc

        text = (response.text or "").strip()
        if not text:
            # Silence or an unintelligible recording. Treated as a failure so it
            # shows up as retryable rather than as a note with an empty transcript.
            raise TranscriptionError(f"{audio_path.name} produced an empty transcript")

        return Transcript(text=text, provider=self.name, model=self._model)
