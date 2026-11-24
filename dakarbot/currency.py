"""Per-guild currency balances, ported from exorium's currency cog. Balances can't go
negative (enforced by the table's CHECK too), and transfers are a single transaction
so money is never lost or duplicated halfway."""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .db import Database

DEFAULT_CURRENCY_NAME = "coins"
WORK_EARN_RANGE = (100, 500)
WORK_LOSS_RANGE = (100, 200)
WORK_COOLDOWN_SECONDS = 500
# Well inside BIGINT and Discord's integer option limit.
MAX_BALANCE = 1_000_000_000_000


class WorkResult(Enum):
    EARNED = "earned"
    NOTHING = "nothing"
    LOST = "lost"


@dataclass(frozen=True)
class WorkOutcome:
    result: WorkResult
    amount: int


def roll_work(rng: random.Random) -> WorkOutcome:
    result = rng.choice(list(WorkResult))
    if result is WorkResult.EARNED:
        return WorkOutcome(result, rng.randint(*WORK_EARN_RANGE))
    if result is WorkResult.LOST:
        return WorkOutcome(result, rng.randint(*WORK_LOSS_RANGE))
    return WorkOutcome(result, 0)


class PaymentError(Exception):
    """Raised with a message that's safe to show the user."""


class InsufficientFunds(PaymentError):
    pass


def validate_payment(payer_id: int, payee_id: int, payee_is_bot: bool, amount: int) -> None:
    if amount <= 0:
        raise PaymentError("The amount must be more than 0.")
    if payer_id == payee_id:
        raise PaymentError("You can't pay yourself.")
    if payee_is_bot:
        raise PaymentError("You can't pay a bot.")


_ADD_SQL = (
    "INSERT INTO balances (guild_id, user_id, amount) VALUES ($1, $2, $3)"
    " ON CONFLICT (guild_id, user_id) DO UPDATE SET amount = balances.amount + EXCLUDED.amount"
    " RETURNING amount"
)


class Balances:
    def __init__(self, db: Database):
        self._db = db

    async def get(self, guild_id: int, user_id: int) -> int:
        row = await self._db.fetchone(
            "SELECT amount FROM balances WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return row["amount"] if row else 0

    async def set(self, guild_id: int, user_id: int, amount: int) -> int:
        if not 0 <= amount <= MAX_BALANCE:
            raise ValueError(f"Balance must be between 0 and {MAX_BALANCE:,}.")
        await self._db.execute(
            "INSERT INTO balances (guild_id, user_id, amount) VALUES ($1, $2, $3)"
            " ON CONFLICT (guild_id, user_id) DO UPDATE SET amount = EXCLUDED.amount",
            guild_id, user_id, amount,
        )
        return amount

    async def reset(self, guild_id: int, user_id: int) -> None:
        await self._db.execute(
            "DELETE FROM balances WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )

    async def apply_work(self, guild_id: int, user_id: int, outcome: WorkOutcome) -> tuple[int, int]:
        """Returns (actual change, new balance); a loss is capped at what the user has."""
        async with self._db.transaction() as conn:
            current = await conn.fetchval(
                "SELECT amount FROM balances WHERE guild_id = $1 AND user_id = $2 FOR UPDATE",
                guild_id, user_id,
            ) or 0
            if outcome.result is WorkResult.EARNED:
                change = min(outcome.amount, MAX_BALANCE - current)
            elif outcome.result is WorkResult.LOST:
                change = -min(outcome.amount, current)
            else:
                change = 0
            if change == 0:
                return 0, current
            if change < 0:
                # The CHECK applies to an upsert's insert row before it merges, so losses
                # (which only happen when a row exists) update in place.
                new = await conn.fetchval(
                    "UPDATE balances SET amount = amount + $3 WHERE guild_id = $1 AND user_id = $2 RETURNING amount",
                    guild_id, user_id, change,
                )
            else:
                new = await conn.fetchval(_ADD_SQL, guild_id, user_id, change)
            return change, new

    async def transfer(self, guild_id: int, payer_id: int, payee_id: int, amount: int) -> tuple[int, int]:
        """Returns (payer's new balance, payee's new balance)."""
        async with self._db.transaction() as conn:
            payer_balance = await conn.fetchval(
                "UPDATE balances SET amount = amount - $3"
                " WHERE guild_id = $1 AND user_id = $2 AND amount >= $3 RETURNING amount",
                guild_id, payer_id, amount,
            )
            if payer_balance is None:
                raise InsufficientFunds("You don't have enough to pay that much.")
            payee_balance = await conn.fetchval(_ADD_SQL, guild_id, payee_id, amount)
            if payee_balance > MAX_BALANCE:
                raise PaymentError("That would put them over the maximum balance.")
            return payer_balance, payee_balance
