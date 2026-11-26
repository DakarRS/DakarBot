"""/rob: a 40% chance to steal 10-30% of someone's balance. Failing fines the robber
10-25% of their own balance (at least MIN_FINE), paid to the target. One attempt per
hour, whatever the result; refused attempts don't use it up."""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..db import Database
from ..economy.balances import credit, debit

SUCCESS_CHANCE = 0.4
STEAL_PERCENT = (10, 30)
FAIL_PERCENT = (10, 25)
MIN_FINE = 50
ROB_MIN_TARGET = 200
ROB_MIN_ROBBER = 100
ROB_COOLDOWN_SECONDS = 60 * 60


class RobError(Exception):
    """Raised with a message that's safe to show the user."""


class RobCooldown(RobError):
    def __init__(self, retry_at: int):
        super().__init__("You're laying low after your last robbery.")
        self.retry_at = retry_at


@dataclass(frozen=True)
class RobRoll:
    success: bool
    percent: int


@dataclass(frozen=True)
class RobResult:
    success: bool
    amount: int
    robber_balance: int
    target_balance: int


def roll_rob(rng: random.Random) -> RobRoll:
    success = rng.random() < SUCCESS_CHANCE
    return RobRoll(success, rng.randint(*(STEAL_PERCENT if success else FAIL_PERCENT)))


class Robberies:
    def __init__(self, db: Database):
        self._db = db

    async def attempt(self, guild_id: int, robber_id: int, target_id: int, now: int, roll: RobRoll) -> RobResult:
        if robber_id == target_id:
            raise RobError("You can't rob yourself.")
        async with self._db.transaction() as conn:
            # Locking both rows in id order keeps opposite robberies from deadlocking.
            rows = await conn.fetch(
                "SELECT user_id, amount FROM balances WHERE guild_id = $1 AND user_id = ANY($2::bigint[])"
                " ORDER BY user_id FOR UPDATE",
                guild_id, [robber_id, target_id],
            )
            held = {row["user_id"]: row["amount"] for row in rows}
            last = await conn.fetchval(
                "SELECT last_attempt FROM rob_cooldowns WHERE guild_id = $1 AND user_id = $2 FOR UPDATE",
                guild_id, robber_id,
            )
            if last is not None and now < last + ROB_COOLDOWN_SECONDS:
                raise RobCooldown(last + ROB_COOLDOWN_SECONDS)
            robber, target = held.get(robber_id, 0), held.get(target_id, 0)
            if robber < ROB_MIN_ROBBER:
                raise RobError(f"You need at least {ROB_MIN_ROBBER:,} to risk a robbery.")
            if target < ROB_MIN_TARGET:
                raise RobError("They don't have enough to be worth robbing.")
            await conn.execute(
                "INSERT INTO rob_cooldowns (guild_id, user_id, last_attempt) VALUES ($1, $2, $3)"
                " ON CONFLICT (guild_id, user_id) DO UPDATE SET last_attempt = EXCLUDED.last_attempt",
                guild_id, robber_id, now,
            )
            if roll.success:
                amount, robber = await credit(conn, guild_id, robber_id, target * roll.percent // 100)
                target = await debit(conn, guild_id, target_id, amount) if amount else target
            else:
                fine = min(robber, max(MIN_FINE, robber * roll.percent // 100))
                amount, target = await credit(conn, guild_id, target_id, fine)
                robber = await debit(conn, guild_id, robber_id, amount) if amount else robber
            return RobResult(roll.success, amount, robber, target)
