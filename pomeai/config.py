"""Configuration and filesystem layout.

Everything the system needs to locate on disk or read from the environment lives
here, so no other module has to guess at paths or re-read .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# The project root - two levels up from this file (pomeai/config.py -> repo root).
BASE_DIR = Path(__file__).resolve().parent.parent

# Env keys grouped by the milestone that first needs them. `check` uses this to
# report what is missing without failing: Milestone 0 requires no secrets at all.
REQUIRED_BY_MILESTONE: dict[str, tuple[str, ...]] = {
    "M1 capture (Telegram)": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID"),
    # Either provider is enough; `check` reports whichever is missing.
    "M2 transcription": ("OPENAI_API_KEY", "GEMINI_API_KEY"),
    "M4 advisor": ("ANTHROPIC_API_KEY",),
}

VALID_WEEK_STARTS = ("monday", "saturday", "sunday")


@dataclass(frozen=True)
class Config:
    """Resolved configuration. Build with `Config.load()`."""

    # Paths
    base_dir: Path
    business_context_file: Path
    sector_context_file: Path
    templates_dir: Path
    report_template_name: str
    data_dir: Path
    audio_dir: Path
    reports_dir: Path
    prompts_dir: Path
    responses_dir: Path
    logs_dir: Path
    db_path: Path

    # Secrets / settings
    telegram_bot_token: str | None
    telegram_owner_chat_id: str | None
    # Outbound only. Never joins the inbound allow-list, so whoever is alerted
    # cannot accidentally send notes into the owner's week.
    telegram_alert_chat_id: str | None
    silence_alert_days: int
    transcription_provider: str
    openai_api_key: str | None
    openai_base_url: str | None
    whisper_model: str
    gemini_api_key: str | None
    gemini_model: str
    anthropic_api_key: str | None
    anthropic_base_url: str | None
    anthropic_model: str
    advisor_effort: str
    advisor_max_tokens: int
    report_timezone: str
    week_start: str
    report_delivery_time: str
    log_level: str

    # Which of REQUIRED_BY_MILESTONE keys are absent, keyed by milestone label.
    missing_keys: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, base_dir: Path | None = None, create_dirs: bool = True) -> "Config":
        root = base_dir or BASE_DIR
        load_dotenv(root / ".env")

        data_dir = root / "data"
        week_start = os.getenv("WEEK_START", "monday").strip().lower()
        if week_start not in VALID_WEEK_STARTS:
            raise ValueError(
                f"WEEK_START must be one of {', '.join(VALID_WEEK_STARTS)}, got {week_start!r}"
            )

        cfg = cls(
            base_dir=root,
            business_context_file=root / "business_context" / "business.md",
            sector_context_file=root / "business_context" / "sector.md",
            templates_dir=root / "templates",
            report_template_name="weekly-report.html",
            data_dir=data_dir,
            audio_dir=data_dir / "audio",
            reports_dir=data_dir / "reports",
            prompts_dir=data_dir / "prompts",
            responses_dir=data_dir / "responses",
            logs_dir=root / "logs",
            db_path=data_dir / "pomeai.db",
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_owner_chat_id=_env("TELEGRAM_OWNER_CHAT_ID"),
            telegram_alert_chat_id=_env("TELEGRAM_ALERT_CHAT_ID"),
            silence_alert_days=int(os.getenv("SILENCE_ALERT_DAYS", "3")),
            transcription_provider=os.getenv("TRANSCRIPTION_PROVIDER", "whisper").strip(),
            openai_api_key=_env("OPENAI_API_KEY"),
            openai_base_url=_env("OPENAI_BASE_URL"),
            whisper_model=os.getenv("WHISPER_MODEL", "whisper-1").strip(),
            gemini_api_key=_env("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip(),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            anthropic_base_url=_env("ANTHROPIC_BASE_URL"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5").strip(),
            advisor_effort=os.getenv("ADVISOR_EFFORT", "high").strip().lower(),
            advisor_max_tokens=int(os.getenv("ADVISOR_MAX_TOKENS", "32000")),
            report_timezone=os.getenv("REPORT_TIMEZONE", "Australia/Sydney").strip(),
            week_start=week_start,
            report_delivery_time=os.getenv("REPORT_DELIVERY_TIME", "08:00").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            missing_keys={
                milestone: [k for k in keys if not _env(k)]
                for milestone, keys in REQUIRED_BY_MILESTONE.items()
            },
        )

        if create_dirs:
            for d in (cfg.audio_dir, cfg.reports_dir, cfg.prompts_dir, cfg.responses_dir, cfg.logs_dir):
                d.mkdir(parents=True, exist_ok=True)

        return cfg

    @property
    def report_template_path(self) -> Path:
        return self.templates_dir / self.report_template_name


def _env(key: str) -> str | None:
    """Read an env var, treating blank/whitespace as unset.

    .env.example ships every key with an empty value, so a copied-but-unfilled
    .env would otherwise look configured.
    """
    value = os.getenv(key)
    if value is None:
        return None
    value = value.strip()
    return value or None
