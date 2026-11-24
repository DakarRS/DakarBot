"""Bot-wide suggestions: stored so the Approve/Deny buttons keep working across restarts
and the submitter can be DMed the decision."""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .db import Database

CUSTOM_ID_PREFIX = "suggestion"


class SuggestionStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class Suggestion:
    id: int
    user_id: int
    guild_id: Optional[int]
    content: str
    status: SuggestionStatus
    channel_id: Optional[int]
    message_id: Optional[int]
    decided_by: Optional[int]
    decision_reason: Optional[str]
    created: int

    @classmethod
    def from_row(cls, row: dict) -> "Suggestion":
        return cls(**{**row, "status": SuggestionStatus(row["status"])})


class Suggestions:
    def __init__(self, db: Database):
        self._db = db

    async def create(self, user_id: int, guild_id: Optional[int], content: str) -> Suggestion:
        row = await self._db.fetchone(
            "INSERT INTO suggestions (user_id, guild_id, content, created) VALUES ($1, $2, $3, $4) RETURNING *",
            user_id, guild_id, content, int(time.time()),
        )
        return Suggestion.from_row(row)

    async def get(self, suggestion_id: int) -> Optional[Suggestion]:
        row = await self._db.fetchone("SELECT * FROM suggestions WHERE id = $1", suggestion_id)
        return Suggestion.from_row(row) if row else None

    async def set_message(self, suggestion_id: int, channel_id: int, message_id: int) -> None:
        await self._db.execute(
            "UPDATE suggestions SET channel_id = $2, message_id = $3 WHERE id = $1",
            suggestion_id, channel_id, message_id,
        )

    async def decide(
        self, suggestion_id: int, status: SuggestionStatus, decided_by: int, reason: Optional[str]
    ) -> Optional[Suggestion]:
        """Returns None if it was already decided, so two clicks can't both win."""
        row = await self._db.fetchone(
            "UPDATE suggestions SET status = $2, decided_by = $3, decision_reason = $4"
            " WHERE id = $1 AND status = 'pending' RETURNING *",
            suggestion_id, status.value, decided_by, reason,
        )
        return Suggestion.from_row(row) if row else None


def decision_custom_id(status: SuggestionStatus, suggestion_id: int) -> str:
    return f"{CUSTOM_ID_PREFIX}:{status.value}:{suggestion_id}"


def parse_decision_custom_id(custom_id: str) -> Optional[tuple[SuggestionStatus, int]]:
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != CUSTOM_ID_PREFIX or not parts[2].isdigit():
        return None
    try:
        status = SuggestionStatus(parts[1])
    except ValueError:
        return None
    if status is SuggestionStatus.PENDING:
        return None
    return status, int(parts[2])
