"""/daily: one claim per UTC day per server. Claiming on consecutive days builds a
streak that raises the reward up to DAILY_MAX_STREAK days; missing a day resets it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from ..db import Database
from .balances import credit

DAILY_BASE = 200
DAILY_STREAK_BONUS = 50
DAILY_MAX_STREAK = 7


def daily_reward(streak: int) -> int:
    return DAILY_BASE + DAILY_STREAK_BONUS * (min(streak, DAILY_MAX_STREAK) - 1)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def next_claim_at(today: date) -> datetime:
    return datetime.combine(today + timedelta(days=1), time(), tzinfo=timezone.utc)


class AlreadyClaimed(Exception):
    pass


@dataclass(frozen=True)
class DailyClaim:
    streak: int
    reward: int
    balance: int


# The WHERE makes a same-day repeat update nothing, so concurrent claims can't both pay.
_CLAIM_SQL = """
INSERT INTO daily_claims (guild_id, user_id, last_claim, streak) VALUES ($1, $2, $3, 1)
ON CONFLICT (guild_id, user_id) DO UPDATE SET
    streak = CASE WHEN daily_claims.last_claim = $3::date - 1 THEN daily_claims.streak + 1 ELSE 1 END,
    last_claim = $3
WHERE daily_claims.last_claim < $3
RETURNING streak
"""


class Daily:
    def __init__(self, db: Database):
        self._db = db

    async def claim(self, guild_id: int, user_id: int, today: date) -> DailyClaim:
        async with self._db.transaction() as conn:
            streak = await conn.fetchval(_CLAIM_SQL, guild_id, user_id, today)
            if streak is None:
                raise AlreadyClaimed
            reward, balance = await credit(conn, guild_id, user_id, daily_reward(streak))
            return DailyClaim(streak, reward, balance)
