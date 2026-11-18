"""Renders the advisor's output into the approved HTML report.

The split is deliberate: the model produces *content*, this module produces
*presentation*. Week labels and the note count are computed here rather than
asked of the model, because they are derivable facts - and a model asked to
format them will eventually get one wrong.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pomeai.config import Config
from pomeai.notes import VoiceNote
from pomeai.weeks import Week

log = logging.getLogger(__name__)

RECIPIENT = "Business owner"


def build_jinja_env(config: Config) -> Environment:
    """Jinja2 environment rooted at templates/."""
    return Environment(
        loader=FileSystemLoader(str(config.templates_dir)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_context(week: Week, notes: list[VoiceNote], sections: dict) -> dict:
    """Merge advisor sections with the values this module owns."""
    context = dict(sections)
    context.update(
        week_label=week.label,
        recipient=RECIPIENT,
        # One value feeds the masthead, the intake badge and the footer, so they
        # cannot disagree the way the original mock's did.
        entry_count=len(notes),
    )
    return context


def render(config: Config, week: Week, notes: list[VoiceNote], sections: dict) -> str:
    env = build_jinja_env(config)
    template = env.get_template(config.report_template_name)
    return template.render(**build_context(week, notes, sections))


def report_path(config: Config, week: Week) -> Path:
    """A dated, unguessable filename.

    The report contains candid material about the business and its people. Once
    it is served over HTTP at Milestone 5, a predictable URL would be readable
    by anyone who guessed the date.
    """
    return config.reports_dir / f"{week.local_start}-{secrets.token_hex(4)}.html"


def save(config: Config, week: Week, notes: list[VoiceNote], sections: dict) -> Path:
    path = report_path(config, week)
    path.write_text(render(config, week, notes, sections), encoding="utf-8")
    log.info("report written to %s", path)
    return path


def find_previous_report(config: Config, week: Week) -> Path | None:
    """The most recent report generated before this week starts.

    Phase 1 has no cross-week memory store - follow-up works by passing the
    previous report's text back into the prompt.
    """
    candidates = [
        p for p in config.reports_dir.glob("*.html")
        if p.stem[:10] < str(week.local_start)
    ]
    return max(candidates, key=lambda p: p.stem) if candidates else None
