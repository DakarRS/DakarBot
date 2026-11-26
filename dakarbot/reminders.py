"""/remind storage. take_due deletes as it reads, so two loop runs can't deliver the
same reminder twice."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .db import Database

MAX_REMINDERS = 25
MIN_SECONDS = 60
MAX_TEXT_LENGTH = 500


class ReminderError(Exception):
    """Raised with a message that's safe to show the user."""


@dataclass(frozen=True)
class Reminder:
    id: int
    user_id: int
    channel_id: Optional[int]
    due: int
    text: str
    created: int


class Reminders:
    def __init__(self, db: Database):
        self._db = db

    async def add(self, user_id: int, channel_id: Optional[int], text: str, now: int, seconds: int) -> Reminder:
        text = text.strip()
        if not text:
            raise ReminderError("Tell me what to remind you about.")
        if seconds < MIN_SECONDS:
            raise ReminderError("Reminders need to be at least a minute away.")
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", user_id)
            count = await conn.fetchval("SELECT count(*) FROM reminders WHERE user_id = $1", user_id)
            if count >= MAX_REMINDERS:
                raise ReminderError(f"You already have {MAX_REMINDERS} reminders. Cancel one first.")
            row = await conn.fetchrow(
                "INSERT INTO reminders (user_id, channel_id, due, text, created) VALUES ($1, $2, $3, $4, $5)"
                " RETURNING *",
                user_id, channel_id, now + seconds, text[:MAX_TEXT_LENGTH], now,
            )
        return Reminder(**row)

    async def list(self, user_id: int) -> list[Reminder]:
        rows = await self._db.fetchall("SELECT * FROM reminders WHERE user_id = $1 ORDER BY due, id", user_id)
        return [Reminder(**row) for row in rows]

    async def cancel(self, user_id: int, reminder_id: int) -> bool:
        return await self._db.execute(
            "DELETE FROM reminders WHERE id = $1 AND user_id = $2", reminder_id, user_id
        ) > 0

    async def take_due(self, now: int) -> list[Reminder]:
        rows = await self._db.fetchall("DELETE FROM reminders WHERE due <= $1 RETURNING *", now)
        return sorted((Reminder(**row) for row in rows), key=lambda r: (r.due, r.id))
