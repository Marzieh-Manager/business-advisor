"""Loads the business context document.

`business_context/business.md` is the product's moat: it is what makes the weekly
analysis specific to this business rather than generic advice. It is injected
into the advisor prompt verbatim, so this module deliberately does no parsing -
the file is authored for the LLM to read, not for code to interpret.
"""

from __future__ import annotations

from pathlib import Path

from pomeai.config import Config

# Markers left in the scaffold for the owner to fill in. Counting them lets
# `check` report how much of the moat is still unwritten.
TODO_MARKER = "[TODO"


class BusinessContextError(RuntimeError):
    """The business context file is missing or unusable."""


def load_business_context(config: Config | None = None) -> str:
    """Return the raw markdown of the business context document."""
    cfg = config or Config.load()
    return _read(cfg.business_context_file)


def load_sector_context(config: Config | None = None) -> str:
    """Category knowledge - how businesses like this one make and lose money.

    Optional by design. It is a lens rather than the moat: without it the
    advisor is still grounded in the owner's own business, just less able to
    say how comparable operators handle the same situation. Returns an empty
    string when the file is absent so a missing lens never blocks a report.
    """
    cfg = config or Config.load()
    path = cfg.sector_context_file
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _read(path: Path) -> str:
    if not path.exists():
        raise BusinessContextError(
            f"Business context file not found at {path}. "
            "This file is required - it is what grounds every weekly report in "
            "this specific business."
        )

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise BusinessContextError(
            f"Business context file at {path} is empty. "
            "An empty context produces generic advice, which is the one thing "
            "this product must not do."
        )
    return text


def count_todos(text: str) -> int:
    """How many unfilled `[TODO ...]` markers remain in the context document."""
    return text.count(TODO_MARKER)
