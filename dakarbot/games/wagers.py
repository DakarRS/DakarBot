"""Taking and paying out bets. Instant games use play(); games that wait on buttons
hold the bet in held_bets so a restart refunds it instead of losing it."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from ..db import Database
from ..economy.balances import MAX_BALANCE, credit, debit

MIN_BET = 10


class BetError(Exception):
    """Raised with a message that's safe to show the user."""


def validate_bet(bet: int) -> None:
    if not MIN_BET <= bet <= MAX_BALANCE:
        raise BetError(f"Bets must be between {MIN_BET} and {MAX_BALANCE:,}.")


def payout_for(bet: int, multiplier: int | Fraction) -> int:
    return math.floor(bet * multiplier)


@dataclass(frozen=True)
class Played:
    payout: int
    balance: int


class Wagers:
    def __init__(self, db: Database):
        self._db = db

    async def play(self, guild_id: int, user_id: int, bet: int, multiplier: int | Fraction) -> Played:
        validate_bet(bet)
        async with self._db.transaction() as conn:
            balance = await debit(conn, guild_id, user_id, bet)
            payout = payout_for(bet, multiplier)
            if payout:
                payout, balance = await credit(conn, guild_id, user_id, payout)
            return Played(payout, balance)

    async def hold(self, guild_id: int, user_id: int, bet: int, game: str) -> int:
        """Takes the bet and returns the hold's id."""
        validate_bet(bet)
        async with self._db.transaction() as conn:
            await debit(conn, guild_id, user_id, bet)
            return await conn.fetchval(
                "INSERT INTO held_bets (guild_id, user_id, amount, game, created) VALUES ($1, $2, $3, $4, $5)"
                " RETURNING id",
                guild_id, user_id, bet, game, int(time.time()),
            )

    async def raise_hold(self, hold_id: int, amount: int) -> Optional[int]:
        """Adds to a held bet. Returns the new balance, or None if the hold is gone."""
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                "UPDATE held_bets SET amount = amount + $2 WHERE id = $1 RETURNING guild_id, user_id", hold_id, amount
            )
            if row is None:
                return None
            return await debit(conn, row["guild_id"], row["user_id"], amount)

    async def settle(self, hold_id: int, payout: int) -> Optional[int]:
        """Closes a hold and pays `payout`. Returns the new balance, or None if it was
        already settled, so a timeout and a button press can't both pay."""
        async with self._db.transaction() as conn:
            row = await conn.fetchrow("DELETE FROM held_bets WHERE id = $1 RETURNING guild_id, user_id", hold_id)
            if row is None:
                return None
            if payout > 0:
                return (await credit(conn, row["guild_id"], row["user_id"], payout))[1]
            return await conn.fetchval(
                "SELECT amount FROM balances WHERE guild_id = $1 AND user_id = $2", row["guild_id"], row["user_id"]
            ) or 0

    async def refund_all(self) -> int:
        """Refunds every open hold. Returns how many there were."""
        async with self._db.transaction() as conn:
            rows = await conn.fetch("DELETE FROM held_bets RETURNING guild_id, user_id, amount")
            for row in rows:
                await credit(conn, row["guild_id"], row["user_id"], row["amount"])
        return len(rows)
