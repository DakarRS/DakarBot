"""Users and servers barred from using the bot. Checked on every command, so the ids
are cached in memory and the table is only read at startup."""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .db import Database


class BlacklistKind(Enum):
    USER = "user"
    SERVER = "server"


@dataclass(frozen=True)
class BlacklistEntry:
    kind: BlacklistKind
    target_id: int
    reason: Optional[str]
    created: int


class Blacklist:
    def __init__(self, db: Database):
        self._db = db
        self._ids: dict[BlacklistKind, set[int]] = {kind: set() for kind in BlacklistKind}

    async def load(self) -> None:
        for kind in BlacklistKind:
            self._ids[kind].clear()
        for row in await self._db.fetchall("SELECT kind, target_id FROM blacklist"):
            self._ids[BlacklistKind(row["kind"])].add(row["target_id"])

    def is_blacklisted(self, kind: BlacklistKind, target_id: Optional[int]) -> bool:
        return target_id in self._ids[kind]

    async def add(self, kind: BlacklistKind, target_id: int, reason: Optional[str]) -> bool:
        """Returns False if it was already blacklisted."""
        added = await self._db.execute(
            "INSERT INTO blacklist (kind, target_id, reason, created) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT DO NOTHING",
            kind.value, target_id, reason, int(time.time()),
        )
        self._ids[kind].add(target_id)
        return added > 0

    async def remove(self, kind: BlacklistKind, target_id: int) -> bool:
        removed = await self._db.execute(
            "DELETE FROM blacklist WHERE kind = $1 AND target_id = $2", kind.value, target_id
        )
        self._ids[kind].discard(target_id)
        return removed > 0

    async def list(self) -> list[BlacklistEntry]:
        rows = await self._db.fetchall("SELECT * FROM blacklist ORDER BY created, target_id")
        return [
            BlacklistEntry(BlacklistKind(r["kind"]), r["target_id"], r["reason"], r["created"]) for r in rows
        ]
