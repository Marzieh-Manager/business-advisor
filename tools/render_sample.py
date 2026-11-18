"""Render the sample weekly report - no API keys, no voice notes, no network.

Feeds `examples/sample-advisor-output.json` (what the advisor model returns for a
fictional week at Harbourline Coffee Co.) through the same normalise, validate
and render steps a real report goes through, and writes
`examples/sample-weekly-report.html`.

    python tools/render_sample.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pomeai import advisor, report  # noqa: E402
from pomeai.config import Config  # noqa: E402
from pomeai.notes import VoiceNote  # noqa: E402
from pomeai.weeks import week_containing  # noqa: E402

SAMPLE_JSON = ROOT / "examples" / "sample-advisor-output.json"
OUTPUT_HTML = ROOT / "examples" / "sample-weekly-report.html"
SAMPLE_WEEK = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)


def main() -> None:
    config = Config.load(create_dirs=False)
    week = week_containing(config, SAMPLE_WEEK)
    sections = advisor._normalise(json.loads(SAMPLE_JSON.read_text(encoding="utf-8")))
    advisor.validate(sections)

    # The report only needs the note count; one placeholder per intake row.
    notes = [
        VoiceNote(
            note_id=f"sample-{i}",
            received_at=SAMPLE_WEEK.isoformat(),
            duration_seconds=0,
            audio_filename="",
        )
        for i, _ in enumerate(sections["intake_summary"])
    ]

    OUTPUT_HTML.write_text(report.render(config, week, notes, sections), encoding="utf-8")
    print(f"sample report written to {OUTPUT_HTML.relative_to(ROOT)} ({week.label})")


if __name__ == "__main__":
    main()
