"""Per-channel settings, mirroring guild_settings.py's single-JSON-blob-per-scope
pattern but keyed by channel. The only setting stored here is /lock's saved prior
permission-overwrite state (modbot's ChannelSettings.lock) — the per-channel invites
override is the only OTHER channel-scoped setting anywhere in modbot's tree, and it
already lives in guild_settings.py's own nested map (see that module's docstring),
so it wasn't worth moving. This one needs its own table: lock state must survive
restarts and be queryable per-guild (/unlock --global needs "every channel in this
guild with saved lock state" without an O(channel count) scan).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from .db import Database

# Matches guild_settings.py's in-memory cache eviction.
CACHE_TTL = 10 * 60


@dataclass
class _CacheEntry:
    data: dict[str, Any]
    expires_at: float


class ChannelSettings:
    def __init__(self, db: Database):
        self._db = db
        self._cache: dict[int, _CacheEntry] = {}

    async def _get_data(self, channel_id: int) -> dict[str, Any]:
        cached = self._cache.get(channel_id)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return cached.data

        row = await self._db.fetchone("SELECT data FROM channel_settings WHERE channel_id = $1", channel_id)
        data = json.loads(row["data"]) if row else {}
        self._cache[channel_id] = _CacheEntry(data=data, expires_at=now + CACHE_TTL)
        return data

    async def _set_data(self, guild_id: int, channel_id: int, data: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO channel_settings (channel_id, guild_id, data) VALUES ($1, $2, $3::jsonb)"
            " ON CONFLICT (channel_id) DO UPDATE SET data = EXCLUDED.data, guild_id = EXCLUDED.guild_id",
            channel_id,
            guild_id,
            json.dumps(data),
        )
        self._cache[channel_id] = _CacheEntry(data=data, expires_at=time.monotonic() + CACHE_TTL)

    async def get_lock(self, channel_id: int) -> dict[str, Optional[bool]]:
        data = await self._get_data(channel_id)
        return data.get("lock", {})

    async def set_lock(self, guild_id: int, channel_id: int, lock: dict[str, Optional[bool]]) -> None:
        data = await self._get_data(channel_id)
        data["lock"] = lock
        await self._set_data(guild_id, channel_id, data)

    async def get_locked_channel_ids(self, guild_id: int) -> list[int]:
        """Every channel in this guild with non-empty saved lock state — for
        `/unlock global:true`."""
        rows = await self._db.fetchall(
            "SELECT channel_id FROM channel_settings"
            " WHERE guild_id = $1 AND data -> 'lock' IS NOT NULL AND data -> 'lock' != '{}'::jsonb",
            guild_id,
        )
        return [row["channel_id"] for row in rows]
