"""How many times each user has done each /action to each other user. Global rather
than per-server, since /action also runs in DMs and servers the bot isn't in."""
from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from .actions import ACTIONS

STATS_LIMIT = 5


@dataclass(frozen=True)
class ActionStats:
    given: list[tuple[str, int]]
    received: list[tuple[str, int]]
    total_given: int
    total_received: int


class ActionCounts:
    def __init__(self, db: Database):
        self._db = db

    async def record(self, action: str, from_id: int, to_id: int) -> int:
        """Returns the new count for this pair and action."""
        if action not in ACTIONS:
            raise ValueError(f"Unknown action: {action}")
        row = await self._db.fetchone(
            "INSERT INTO action_counts (from_id, to_id, action, count) VALUES ($1, $2, $3, 1)"
            " ON CONFLICT (from_id, to_id, action) DO UPDATE SET count = action_counts.count + 1"
            " RETURNING count",
            from_id, to_id, action,
        )
        return row["count"]

    async def _side(self, column: str, user_id: int) -> tuple[list[tuple[str, int]], int]:
        rows = await self._db.fetchall(
            f"SELECT action, sum(count)::int AS total FROM action_counts WHERE {column} = $1"
            " GROUP BY action ORDER BY total DESC, action",
            user_id,
        )
        pairs = [(row["action"], row["total"]) for row in rows]
        return pairs[:STATS_LIMIT], sum(total for _, total in pairs)

    async def stats(self, user_id: int) -> ActionStats:
        given, total_given = await self._side("from_id", user_id)
        received, total_received = await self._side("to_id", user_id)
        return ActionStats(given, received, total_given, total_received)
