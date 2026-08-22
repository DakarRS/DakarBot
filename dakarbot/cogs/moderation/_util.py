"""Small shared helpers used by several command/modal files in this cog package.
Underscore-prefixed per the codebase's existing convention for "internal, not a
command" modules (not functionally required here — DakarBot._load_cogs() only
inspects direct children of dakarbot/cogs/, and this package is already loaded as
one unit via __init__.py — but kept for readability/consistency).
"""
from typing import Optional

from dakarbot.moderation.duration import parse_duration

DEFAULT_DELETE_SECONDS = 3600


def parse_duration_option(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    parsed = parse_duration(text)
    if parsed is None:
        raise ValueError(f"Couldn't parse duration: {text!r}")
    return parsed
