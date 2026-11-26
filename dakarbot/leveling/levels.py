"""Per-server XP from chatting. A new highest level pays LEVEL_UP_COINS × level and
grants any role reward for it; /cash-in trades XP for coins at XP_PER_COIN."""
from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ..economy.balances import credit
from .curve import level_for_xp

AWARD_COOLDOWN_SECONDS = 60
LEVEL_UP_COINS = 100
XP_PER_COIN = 1
MAX_REWARDS = 25
MAX_REWARD_LEVEL = 500


class LevelError(Exception):
    """Raised with a message that's safe to show the user."""


def level_up_payout(levels: list[int]) -> int:
    return LEVEL_UP_COINS * sum(levels)


@dataclass(frozen=True)
class Award:
    xp: int
    level: int
    new_levels: list[int]
    payout: int
    balance: int
    role_ids: list[int]


@dataclass(frozen=True)
class LevelInfo:
    xp: int
    level: int


@dataclass(frozen=True)
class CashIn:
    xp: int
    level: int
    coins: int
    balance: int


# The WHERE skips awards inside the cooldown; the row lock makes concurrent ones wait.
_AWARD_SQL = """
INSERT INTO levels (guild_id, user_id, xp, last_award) VALUES ($1, $2, $3, $4)
ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = levels.xp + EXCLUDED.xp, last_award = EXCLUDED.last_award
WHERE levels.last_award <= $4 - $5::bigint
RETURNING xp, max_level
"""


class Levels:
    def __init__(self, db: Database):
        self._db = db

    async def award(self, guild_id: int, user_id: int, amount: int, now: int) -> Award | None:
        """Returns None when the member earned XP too recently."""
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(_AWARD_SQL, guild_id, user_id, amount, now, AWARD_COOLDOWN_SECONDS)
            if row is None:
                return None
            level = level_for_xp(row["xp"])
            new_levels = list(range(row["max_level"] + 1, level + 1))
            payout, balance, role_ids = 0, 0, []
            if new_levels:
                await conn.execute(
                    "UPDATE levels SET max_level = $3 WHERE guild_id = $1 AND user_id = $2", guild_id, user_id, level
                )
                payout, balance = await credit(conn, guild_id, user_id, level_up_payout(new_levels))
                role_ids = [r["role_id"] for r in await conn.fetch(
                    "SELECT role_id FROM level_rewards WHERE guild_id = $1 AND level = ANY($2::int[]) ORDER BY level",
                    guild_id, new_levels,
                )]
            return Award(row["xp"], level, new_levels, payout, balance, role_ids)

    async def get(self, guild_id: int, user_id: int) -> LevelInfo:
        row = await self._db.fetchone(
            "SELECT xp FROM levels WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        xp = row["xp"] if row else 0
        return LevelInfo(xp, level_for_xp(xp))

    async def cash_in(self, guild_id: int, user_id: int, xp: int) -> CashIn:
        async with self._db.transaction() as conn:
            current = await conn.fetchval(
                "SELECT xp FROM levels WHERE guild_id = $1 AND user_id = $2 FOR UPDATE", guild_id, user_id
            ) or 0
            if xp <= 0:
                raise LevelError("Cash in at least 1 XP.")
            if xp > current:
                raise LevelError(f"You only have {current:,} XP.")
            coins, balance = await credit(conn, guild_id, user_id, xp // XP_PER_COIN)
            if coins == 0:
                raise LevelError("Your balance is already at the maximum.")
            left = current - coins * XP_PER_COIN
            await conn.execute(
                "UPDATE levels SET xp = $3 WHERE guild_id = $1 AND user_id = $2", guild_id, user_id, left
            )
            return CashIn(left, level_for_xp(left), coins, balance)

    async def set_reward(self, guild_id: int, level: int, role_id: int) -> None:
        if not 1 <= level <= MAX_REWARD_LEVEL:
            raise LevelError(f"The level must be between 1 and {MAX_REWARD_LEVEL}.")
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", guild_id)
            existing = await conn.fetch("SELECT level FROM level_rewards WHERE guild_id = $1", guild_id)
            if len(existing) >= MAX_REWARDS and level not in {r["level"] for r in existing}:
                raise LevelError(f"There are already {MAX_REWARDS} level rewards. Remove one first.")
            await conn.execute(
                "INSERT INTO level_rewards (guild_id, level, role_id) VALUES ($1, $2, $3)"
                " ON CONFLICT (guild_id, level) DO UPDATE SET role_id = EXCLUDED.role_id",
                guild_id, level, role_id,
            )

    async def remove_reward(self, guild_id: int, level: int) -> bool:
        return await self._db.execute(
            "DELETE FROM level_rewards WHERE guild_id = $1 AND level = $2", guild_id, level
        ) > 0

    async def rewards(self, guild_id: int) -> list[tuple[int, int]]:
        rows = await self._db.fetchall(
            "SELECT level, role_id FROM level_rewards WHERE guild_id = $1 ORDER BY level", guild_id
        )
        return [(r["level"], r["role_id"]) for r in rows]
