"""Per-server shop. An item either grants a role (bought once) or is a collectible
that stacks in the buyer's inventory. Removing an item only hides it from the shop,
so past purchases still show in inventories."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import asyncpg

from ..db import Database
from .balances import MAX_BALANCE, debit

# Matches Discord's autocomplete limit, so /buy can offer every item.
MAX_ITEMS = 25
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 200


class ShopError(Exception):
    """Raised with a message that's safe to show the user."""


@dataclass(frozen=True)
class ShopItem:
    id: int
    guild_id: int
    name: str
    price: int
    role_id: Optional[int]
    description: Optional[str]


@dataclass(frozen=True)
class Purchase:
    item: ShopItem
    balance: int


@dataclass(frozen=True)
class InventoryEntry:
    name: str
    role_id: Optional[int]
    count: int


def _item(row) -> ShopItem:
    return ShopItem(**{k: row[k] for k in ShopItem.__dataclass_fields__})


class Shop:
    def __init__(self, db: Database):
        self._db = db

    async def add(
        self, guild_id: int, name: str, price: int, *, role_id: Optional[int] = None,
        description: Optional[str] = None,
    ) -> ShopItem:
        name = name.strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise ShopError(f"Item names must be 1-{MAX_NAME_LENGTH} characters.")
        if not 0 < price <= MAX_BALANCE:
            raise ShopError(f"The price must be between 1 and {MAX_BALANCE:,}.")
        async with self._db.transaction() as conn:
            # Serializes adds per guild so the size limit holds under concurrent adds.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", guild_id)
            count = await conn.fetchval(
                "SELECT count(*) FROM shop_items WHERE guild_id = $1 AND active", guild_id
            )
            if count >= MAX_ITEMS:
                raise ShopError(f"The shop is full ({MAX_ITEMS} items). Remove one first.")
            try:
                row = await conn.fetchrow(
                    "INSERT INTO shop_items (guild_id, name, price, role_id, description)"
                    " VALUES ($1, $2, $3, $4, $5) RETURNING *",
                    guild_id, name, price, role_id, description,
                )
            except asyncpg.UniqueViolationError:
                raise ShopError(f"The shop already has an item called {name}.") from None
        return _item(row)

    async def remove(self, guild_id: int, name: str) -> bool:
        return await self._db.execute(
            "UPDATE shop_items SET active = FALSE WHERE guild_id = $1 AND lower(name) = lower($2) AND active",
            guild_id, name.strip(),
        ) > 0

    async def list(self, guild_id: int) -> list[ShopItem]:
        rows = await self._db.fetchall(
            "SELECT * FROM shop_items WHERE guild_id = $1 AND active ORDER BY price, lower(name)", guild_id
        )
        return [_item(row) for row in rows]

    async def get(self, guild_id: int, name: str) -> Optional[ShopItem]:
        row = await self._db.fetchone(
            "SELECT * FROM shop_items WHERE guild_id = $1 AND lower(name) = lower($2) AND active",
            guild_id, name.strip(),
        )
        return _item(row) if row else None

    async def buy(
        self, guild_id: int, user_id: int, name: str, grant: Callable[[ShopItem], Awaitable[None]]
    ) -> Purchase:
        """Charges, records, then calls `grant`; if grant raises, the whole purchase rolls back."""
        item = await self.get(guild_id, name)
        if item is None:
            raise ShopError(f"The shop has no item called {name}.")
        async with self._db.transaction() as conn:
            # The debit locks the buyer's balance row, so a concurrent buy waits here and
            # then sees this purchase in the ownership check below.
            balance = await debit(conn, guild_id, user_id, item.price)
            if item.role_id is not None and await conn.fetchval(
                "SELECT 1 FROM purchases WHERE guild_id = $1 AND user_id = $2 AND item_id = $3",
                guild_id, user_id, item.id,
            ):
                raise ShopError(f"You already own {item.name}.")
            await conn.execute(
                "INSERT INTO purchases (guild_id, user_id, item_id, price, created) VALUES ($1, $2, $3, $4, $5)",
                guild_id, user_id, item.id, item.price, int(time.time()),
            )
            await grant(item)
        return Purchase(item, balance)

    async def inventory(self, guild_id: int, user_id: int) -> list[InventoryEntry]:
        rows = await self._db.fetchall(
            "SELECT i.name, i.role_id, count(*) AS count FROM purchases p"
            " JOIN shop_items i ON i.id = p.item_id"
            " WHERE p.guild_id = $1 AND p.user_id = $2"
            " GROUP BY i.id ORDER BY lower(i.name)",
            guild_id, user_id,
        )
        return [InventoryEntry(row["name"], row["role_id"], row["count"]) for row in rows]
