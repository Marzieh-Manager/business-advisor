"""The advisor's memory.

Without this the system meets the owner for the first time every Monday: it can
comment on a week but it cannot say "this is the fourth week running you have
raised the warehouse hire and nothing has moved". That sentence is the whole
difference between a weekly commentary and an advisor.

Two rules shape this module:

- **Code counts, the model judges.** Recurrence, age and open counts are facts,
  so they are computed here and handed to the prompt. Asking a model to count
  weeks invites it to be confidently wrong about something checkable.
- **A decision is never deleted.** Statuses change; history does not. The topic
  archive is only useful if it is complete.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from pomeai.config import Config
from pomeai.db import session

log = logging.getLogger(__name__)

# `open` is the starting state. The three the advisor may assign match the
# follow-up statuses the report template already renders.
STATUSES = ("open", "done", "partial", "not_started", "dropped")

# Statuses that mean the decision is still live and should be carried forward.
LIVE_STATUSES = ("open", "partial", "not_started")

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_words: int = 4) -> str:
    """A stable topic key: 'Warehouse Hire!' -> 'warehouse-hire'.

    Capped in length because a slug is a join key. If a whole priority
    sentence becomes the slug, next week's `warehouse-hire` will not match
    `get-the-warehouse-hire-out-of-the-loop-and-into-a-trial-shift`, and
    recurrence - the reason this store exists - silently never fires.
    """
    words = [w for w in _SLUG.sub("-", text.strip().lower()).split("-") if w]
    return "-".join(words[:max_words]) or "general"


def ordinal(n: int) -> str:
    """1 -> 1st, 2 -> 2nd, 4 -> 4th. Used for "in its 4th week"."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass
class Decision:
    """One thing the advisor told the owner to do."""

    topic: str
    subject: str
    detail: str
    week_start: str
    decision_id: str = field(default_factory=lambda: secrets.token_hex(6))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    rank: int | None = None
    status: str = "open"
    status_note: str | None = None
    resolved_week: str | None = None
    report_filename: str | None = None

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES


_COLUMNS = (
    "decision_id", "week_start", "created_at", "topic", "subject", "detail",
    "rank", "status", "status_note", "resolved_week", "report_filename",
)


def _row_to_decision(row) -> Decision:
    return Decision(**{key: row[key] for key in _COLUMNS})


def record(config: Config, decisions: list[Decision]) -> int:
    """Store newly-issued decisions. Existing ids are left alone."""
    if not decisions:
        return 0
    placeholders = ", ".join("?" * len(_COLUMNS))
    with session(config) as conn:
        conn.executemany(
            f"INSERT OR IGNORE INTO decisions ({', '.join(_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [tuple(getattr(d, c) for c in _COLUMNS) for d in decisions],
        )
    log.info("recorded %d decision(s)", len(decisions))
    return len(decisions)


def load(
    config: Config,
    *,
    topic: str | None = None,
    live_only: bool = False,
    before_week: str | None = None,
) -> list[Decision]:
    """Decisions, oldest first."""
    clauses, params = [], []
    if topic:
        clauses.append("topic = ?")
        params.append(topic)
    if live_only:
        clauses.append(f"status IN ({', '.join('?' * len(LIVE_STATUSES))})")
        params.extend(LIVE_STATUSES)
    if before_week:
        clauses.append("week_start < ?")
        params.append(before_week)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(config) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM decisions {where} "
            "ORDER BY week_start ASC, rank ASC, created_at ASC",
            params,
        ).fetchall()
    return [_row_to_decision(r) for r in rows]


def set_status(
    config: Config, decision_id: str, status: str, note: str | None, week_start: str
) -> None:
    """Update one decision's status. Raises if the id is unknown."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r} - expected one of {STATUSES}")
    with session(config) as conn:
        changed = conn.execute(
            "UPDATE decisions SET status = ?, status_note = ?, resolved_week = ? "
            "WHERE decision_id = ?",
            (status, note, week_start, decision_id),
        ).rowcount
    if not changed:
        raise KeyError(f"no decision with id {decision_id!r}")


def weeks_between(earlier: str, later: str) -> int:
    """Whole weeks from one Monday to another. Negative if the order is wrong."""
    delta = date.fromisoformat(later) - date.fromisoformat(earlier)
    return delta.days // 7


@dataclass
class TopicThread:
    """Every decision on one topic, and how long it has been running."""

    topic: str
    decisions: list[Decision]

    @property
    def first_week(self) -> str:
        return self.decisions[0].week_start

    @property
    def latest_week(self) -> str:
        return self.decisions[-1].week_start

    @property
    def live(self) -> list[Decision]:
        return [d for d in self.decisions if d.is_live]

    @property
    def is_open(self) -> bool:
        return bool(self.live)

    def weeks_running(self, as_of_week: str) -> int:
        """How many weeks since this topic was first raised, inclusive."""
        return weeks_between(self.first_week, as_of_week) + 1

    def times_raised(self) -> int:
        """Distinct weeks this topic produced a decision."""
        return len({d.week_start for d in self.decisions})


def threads(config: Config, *, as_of_week: str | None = None) -> list[TopicThread]:
    """Group every decision by topic, longest-running first."""
    grouped: dict[str, list[Decision]] = {}
    for decision in load(config):
        if as_of_week and decision.week_start > as_of_week:
            continue
        grouped.setdefault(decision.topic, []).append(decision)
    result = [TopicThread(topic=t, decisions=d) for t, d in grouped.items()]
    result.sort(key=lambda th: (th.first_week, th.topic))
    return result


def history_block(config: Config, week_start: str) -> str:
    """The memory the advisor gets: open decisions, and how long they have run.

    Rendered as text rather than passed as JSON because it goes into a prompt,
    and because the counts are the point - they should be impossible to miss.
    Returns an empty string when there is no history, so the caller can leave the
    section out entirely rather than tell the model "no history" twice.
    """
    prior = [th for th in threads(config, as_of_week=week_start)
             if th.first_week < week_start]
    if not prior:
        return ""

    open_threads = [th for th in prior if th.is_open]
    closed = [th for th in prior if not th.is_open]

    lines: list[str] = []

    if open_threads:
        lines.append("## Still open")
        lines.append("")
        for thread in sorted(
            open_threads,
            key=lambda t: (-t.weeks_running(week_start), t.topic),
        ):
            weeks = thread.weeks_running(week_start)
            raised = thread.times_raised()
            age = f"in its {ordinal(weeks)} week"
            if raised > 1:
                age += f", raised in {raised} of them"
            lines.append(f"### {thread.topic} ({age})")

            # The same commitment re-issued week after week is several rows,
            # which is how recurrence gets counted - but the model should be
            # asked to follow up on it once, using the newest id.
            newest: dict[str, Decision] = {}
            repeats: dict[str, int] = {}
            for decision in thread.live:
                key = decision.subject.strip().lower()
                newest[key] = decision
                repeats[key] = repeats.get(key, 0) + 1

            for key, decision in newest.items():
                status = decision.status.replace("_", " ")
                # The id has to appear in the text: the model is asked to
                # return a follow-up keyed by it, and cannot cite what it
                # never saw.
                line = (
                    f"- id `{decision.decision_id}` [{status}] "
                    f"{decision.subject}: {decision.detail}"
                )
                if repeats[key] > 1:
                    line += f" (issued {repeats[key]} weeks running)"
                lines.append(line)
                if decision.status_note:
                    lines.append(f"  last recorded: {decision.status_note}")
            lines.append("")

    if closed:
        lines.append("## Closed")
        lines.append("")
        for thread in closed:
            done = thread.decisions[-1]
            lines.append(f"- {thread.topic}: {done.subject} ({done.status})")
        lines.append("")

    stale = [th for th in open_threads if th.weeks_running(week_start) >= 3]
    if stale:
        lines.append("## Running three weeks or longer, still not closed")
        lines.append("")
        for thread in stale:
            lines.append(
                f"- {thread.topic}: {thread.weeks_running(week_start)} weeks, "
                f"raised in {thread.times_raised()} of them"
            )
        lines.append("")

    return "\n".join(lines).strip()


def from_sections(sections: dict, week_start: str, report_filename: str) -> list[Decision]:
    """Turn a report's priorities into tracked decisions.

    Only `next_priorities` is recorded. The action plan is this week's schedule
    and is not meant to outlive the week; the priorities are the commitments.
    """
    decisions = []
    for priority in sections.get("next_priorities", []) or []:
        subject = str(priority.get("priority", "")).strip()
        if not subject:
            continue
        topic = priority.get("topic") or slugify(subject)
        rank = priority.get("rank")
        decisions.append(
            Decision(
                topic=slugify(str(topic)),
                subject=subject,
                detail=str(priority.get("detail", "")).strip(),
                week_start=week_start,
                rank=int(rank) if isinstance(rank, int) or str(rank).isdigit() else None,
                report_filename=report_filename,
            )
        )
    return decisions


def apply_follow_up(config: Config, sections: dict, week_start: str) -> int:
    """Write the advisor's follow-up verdicts back onto the tracked decisions.

    The advisor is given each open decision's id and returns a status for it.
    An unknown id is logged and skipped rather than raised: a malformed
    follow-up should not throw away a report that is otherwise fine.
    """
    follow_up = sections.get("follow_up") or {}
    updated = 0
    for entry in follow_up.get("entries", []) or []:
        decision_id = entry.get("decision_id")
        status = entry.get("status")
        if not decision_id or status not in STATUSES:
            continue
        try:
            set_status(config, decision_id, status, entry.get("note"), week_start)
            updated += 1
        except KeyError:
            log.warning("follow-up referenced unknown decision %r", decision_id)
    return updated
