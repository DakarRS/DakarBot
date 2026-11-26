"""XP curve: reaching level n+1 from n takes 5n² + 50n + 100 XP."""
from __future__ import annotations

import random

XP_RANGE = (15, 25)
BAR_WIDTH = 12


def xp_for_next(level: int) -> int:
    return 5 * level * level + 50 * level + 100


def progress(xp: int) -> tuple[int, int, int]:
    """Returns (level, XP into that level, XP the level needs)."""
    level = 0
    while xp >= xp_for_next(level):
        xp -= xp_for_next(level)
        level += 1
    return level, xp, xp_for_next(level)


def level_for_xp(xp: int) -> int:
    return progress(xp)[0]


def roll_xp(rng: random.Random) -> int:
    return rng.randint(*XP_RANGE)


def level_label(xp: int) -> str:
    return f"Level {level_for_xp(xp)} ({xp:,} XP)"


def progress_bar(done: int, total: int, width: int = BAR_WIDTH) -> str:
    filled = min(width, done * width // total) if total else width
    return "█" * filled + "░" * (width - filled)
