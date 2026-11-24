"""Human-readable duration parsing/formatting, matching modbot's src/util/timeutils.js.

Supports compound durations like "1d 12h". Units are case-insensitive except the
single-letter forms "M" (month) and "m" (minute), which collide once lowercased —
modbot disambiguates those two specifically by case, so we do too.
"""
from __future__ import annotations

import math
import re

_YEAR = 365 * 24 * 3600
_MONTH = 30 * 24 * 3600
_WEEK = 7 * 24 * 3600
_DAY = 24 * 3600
_HOUR = 3600
_MINUTE = 60
_SECOND = 1

# Exact-case entries here are consulted before the case-insensitive fallback below,
# solely to disambiguate "M" (month) from "m" (minute).
_EXACT_CASE_UNITS = {"M": _MONTH, "m": _MINUTE}

_CASE_INSENSITIVE_UNITS = {
    "y": _YEAR, "yr": _YEAR, "yrs": _YEAR, "year": _YEAR, "years": _YEAR,
    "month": _MONTH, "months": _MONTH,
    "w": _WEEK, "week": _WEEK, "weeks": _WEEK,
    "d": _DAY, "day": _DAY, "days": _DAY,
    "h": _HOUR, "hr": _HOUR, "hrs": _HOUR, "hour": _HOUR, "hours": _HOUR,
    "min": _MINUTE, "mins": _MINUTE, "minute": _MINUTE, "minutes": _MINUTE,
    "s": _SECOND, "sec": _SECOND, "secs": _SECOND, "second": _SECOND, "seconds": _SECOND,
}

# Anything longer overflows timedelta/BIGINT downstream, and is permanent in practice.
MAX_DURATION_SECONDS = 10 * _YEAR


class DurationTooLong(ValueError):
    def __init__(self) -> None:
        super().__init__("That duration is too long. The maximum is 10 years; leave it empty for permanent.")


_UNIT_PATTERN = re.compile(r"(?P<value>\d*\.?\d+)\s*(?P<unit>[A-Za-z]+)")


def parse_duration(text: str) -> int | None:
    """Returns total seconds, or None if no valid unit was found anywhere in `text`.
    Raises DurationTooLong past MAX_DURATION_SECONDS."""
    if not text:
        return None

    total = 0.0
    matched = False
    for match in _UNIT_PATTERN.finditer(text):
        unit = match.group("unit")
        seconds = _EXACT_CASE_UNITS.get(unit)
        if seconds is None:
            seconds = _CASE_INSENSITIVE_UNITS.get(unit.lower())
        if seconds is None:
            continue
        matched = True
        total += float(match.group("value")) * seconds

    if not matched:
        return None
    if not math.isfinite(total) or total > MAX_DURATION_SECONDS:
        raise DurationTooLong()
    return int(total)


_FORMAT_UNITS = (
    ("year", _YEAR),
    ("month", _MONTH),
    ("week", _WEEK),
    ("day", _DAY),
    ("hour", _HOUR),
    ("minute", _MINUTE),
    ("second", _SECOND),
)


def format_duration(duration: int) -> str:
    """Greedy largest-unit-first formatting, e.g. 90000 -> "1 day 1 hour"."""
    if duration <= 0:
        return "0 seconds"

    parts: list[str] = []
    remaining = duration
    for name, unit_seconds in _FORMAT_UNITS:
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            remaining -= count * unit_seconds
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
    return " ".join(parts)
