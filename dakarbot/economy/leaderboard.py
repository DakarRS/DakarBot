"""Per-server rankings for /leaderboard. Ties share a rank (1, 1, 3)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from ..db import Database

DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class Entry:
    rank: int
    user_id: int
    value: int


@dataclass(frozen=True)
class Ranking:
    top: list[Entry]
    caller: Optional[Entry]


async def _rank(db: Database, source: str, args: tuple, caller_id: int, limit: int) -> Ranking:
    """`source` selects (user_id, value) rows using $1.. from `args`."""
    ranked = (
        f"SELECT user_id, value, RANK() OVER (ORDER BY value DESC) AS rank FROM ({source}) s"
        " WHERE value > 0"
    )
    n = len(args)
    rows = await db.fetchall(
        f"SELECT * FROM ({ranked}) r WHERE rank <= ${n + 1} OR user_id = ${n + 2} ORDER BY rank, user_id",
        *args, limit, caller_id,
    )
    entries = [Entry(row["rank"], row["user_id"], row["value"]) for row in rows]
    top = [e for e in entries if e.rank <= limit][:limit]
    caller = next((e for e in entries if e.user_id == caller_id), None)
    return Ranking(top, caller)


async def richest(db: Database, guild_id: int, caller_id: int, limit: int = DEFAULT_LIMIT) -> Ranking:
    return await _rank(
        db, "SELECT user_id, amount AS value FROM balances WHERE guild_id = $1", (guild_id,), caller_id, limit
    )


async def streaks(
    db: Database, guild_id: int, caller_id: int, today: date, limit: int = DEFAULT_LIMIT
) -> Ranking:
    """Only streaks still alive: claimed today or yesterday."""
    return await _rank(
        db,
        "SELECT user_id, streak AS value FROM daily_claims WHERE guild_id = $1 AND last_claim >= $2",
        (guild_id, today - timedelta(days=1)), caller_id, limit,
    )
