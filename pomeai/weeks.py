"""Week boundaries.

The reporting week runs Monday to Sunday in the business's local timezone
(confirmed with the owner). The report is delivered at the START of a week and
covers the week just finished, so the owner reads it while planning the week
ahead.

Boundaries are computed in local time and returned as UTC, because notes are
stored in UTC. Doing it the other way round would put the week boundary at the
wrong hour whenever daylight saving shifts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pomeai.config import Config
from pomeai.formatting import MONTHS, ordinal

# datetime.weekday(): Monday == 0.
WEEK_START_INDEX = {"monday": 0, "saturday": 5, "sunday": 6}

@dataclass(frozen=True)
class Week:
    """One reporting week, half-open: `start <= note < end`."""

    start: datetime  # UTC, inclusive
    end: datetime  # UTC, exclusive
    local_start: date
    local_end: date  # the last day *inside* the week, for labelling

    @property
    def label(self) -> str:
        """e.g. `3–9 August 2026`, collapsing a shared month."""
        start_month = MONTHS[self.local_start.month - 1]
        end_month = MONTHS[self.local_end.month - 1]
        if self.local_start.month == self.local_end.month:
            return (f"{ordinal(self.local_start.day)}–{ordinal(self.local_end.day)} "
                    f"{end_month} {self.local_end.year}")
        return (f"{ordinal(self.local_start.day)} {start_month} – "
                f"{ordinal(self.local_end.day)} {end_month} {self.local_end.year}")

    def __contains__(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def week_containing(config: Config, moment: datetime | None = None) -> Week:
    """The reporting week that `moment` falls inside. Defaults to now."""
    tz = ZoneInfo(config.report_timezone)
    moment = (moment or datetime.now(timezone.utc)).astimezone(tz)

    start_index = WEEK_START_INDEX[config.week_start]
    days_since_start = (moment.weekday() - start_index) % 7
    local_start_date = moment.date() - timedelta(days=days_since_start)

    # Build local midnight, then convert - so the boundary lands at local
    # midnight even across a daylight-saving change.
    start_local = datetime.combine(local_start_date, time.min, tzinfo=tz)
    end_local = datetime.combine(local_start_date + timedelta(days=7), time.min, tzinfo=tz)

    return Week(
        start=start_local.astimezone(timezone.utc),
        end=end_local.astimezone(timezone.utc),
        local_start=local_start_date,
        local_end=local_start_date + timedelta(days=6),
    )


def current_week(config: Config) -> Week:
    return week_containing(config)


def last_week(config: Config) -> Week:
    """The week just finished - what a start-of-week report covers."""
    this_week = current_week(config)
    return week_containing(config, this_week.start - timedelta(seconds=1))


def week_offset(config: Config, offset: int) -> Week:
    """`0` is the current week, `-1` last week, `-2` the week before, and so on."""
    reference = current_week(config).start + timedelta(weeks=offset)
    return week_containing(config, reference)
