"""Display formatting for the report and the bot.

The owner decided the product is English end to end, so weekday names, dates
and durations are formatted here rather than being asked of the model - they
are derivable facts, and a model asked to format them will eventually get one
wrong.
"""

from __future__ import annotations

from datetime import datetime

# Indexed by datetime.weekday(): Monday == 0.
WEEKDAYS: tuple[str, ...] = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

MONTHS: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def weekday(moment: datetime) -> str:
    """Name of the weekday. Pass a localised datetime, not UTC."""
    return WEEKDAYS[moment.weekday()]


def duration(seconds: int) -> str:
    """Format a voice-note length as `m:ss`."""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def ordinal(day: int) -> str:
    """`1st`, `2nd`, `3rd`, `4th` - the way a date reads in prose."""
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"
