"""Per-guild settings: a single cached JSON blob per guild, mirroring modbot's
Settings/GuildSettings pattern (src/settings/GuildSettings.js).

This only exposes the typed accessors other cogs need programmatically. The /settings
cog is the command surface for editing these values, built on top of this same store.

The only channel-scoped setting anywhere in modbot's /settings tree is the per-channel
invites allow/forbid override — everything else is guild-wide. That didn't justify a
whole separate ChannelSettings table/class (modbot has one; we don't), so it's stored
as a nested channel_id -> bool map inside this same guild blob instead.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from .economy.balances import DEFAULT_CURRENCY_NAME
from .db import Database
from .moderation.models import DEFAULT_LADDER, Punishment, PunishmentAction

# Matches modbot's 10-minute in-memory settings cache eviction.
CACHE_TTL = 10 * 60

# Defaults, matching GuildSettings.js's #defaults.
DEFAULT_INVITES_ALLOWED = True
DEFAULT_LINK_COOLDOWN = 10
DEFAULT_ATTACHMENT_COOLDOWN = 10
DEFAULT_CAPS_ENABLED = False
DEFAULT_ANTI_SPAM = 10
DEFAULT_SIMILAR_MESSAGES = 3


@dataclass
class _CacheEntry:
    data: dict[str, Any]
    expires_at: float


class GuildSettings:
    def __init__(self, db: Database):
        self._db = db
        self._cache: dict[int, _CacheEntry] = {}

    async def _get_data(self, guild_id: int) -> dict[str, Any]:
        cached = self._cache.get(guild_id)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return cached.data

        row = await self._db.fetchone("SELECT data FROM guild_settings WHERE guild_id = $1", guild_id)
        data = json.loads(row["data"]) if row else {}
        self._cache[guild_id] = _CacheEntry(data=data, expires_at=now + CACHE_TTL)
        return data

    async def _set_data(self, guild_id: int, data: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO guild_settings (guild_id, data) VALUES ($1, $2::jsonb)"
            " ON CONFLICT (guild_id) DO UPDATE SET data = EXCLUDED.data",
            guild_id,
            json.dumps(data),
        )
        self._cache[guild_id] = _CacheEntry(data=data, expires_at=time.monotonic() + CACHE_TTL)

    async def _get_field(self, guild_id: int, key: str, default: Any = None) -> Any:
        data = await self._get_data(guild_id)
        return data.get(key, default)

    async def _set_field(self, guild_id: int, key: str, value: Any) -> None:
        data = await self._get_data(guild_id)
        data[key] = value
        await self._set_data(guild_id, data)

    # ---- log channels ----

    async def get_log_channel_id(self, guild_id: int) -> Optional[int]:
        return await self._get_field(guild_id, "log_channel_id")

    async def set_log_channel_id(self, guild_id: int, channel_id: Optional[int]) -> None:
        await self._set_field(guild_id, "log_channel_id", channel_id)

    async def get_message_log_channel_id(self, guild_id: int) -> Optional[int]:
        return await self._get_field(guild_id, "message_log_channel_id")

    async def set_message_log_channel_id(self, guild_id: int, channel_id: Optional[int]) -> None:
        await self._set_field(guild_id, "message_log_channel_id", channel_id)

    async def get_join_log_channel_id(self, guild_id: int) -> Optional[int]:
        return await self._get_field(guild_id, "join_log_channel_id")

    async def set_join_log_channel_id(self, guild_id: int, channel_id: Optional[int]) -> None:
        await self._set_field(guild_id, "join_log_channel_id", channel_id)

    async def get_currency_log_channel_id(self, guild_id: int) -> Optional[int]:
        return await self._get_field(guild_id, "currency_log_channel_id")

    async def set_currency_log_channel_id(self, guild_id: int, channel_id: Optional[int]) -> None:
        await self._set_field(guild_id, "currency_log_channel_id", channel_id)

    # ---- currency ----

    async def get_currency_name(self, guild_id: int) -> str:
        return await self._get_field(guild_id, "currency_name") or DEFAULT_CURRENCY_NAME

    async def set_currency_name(self, guild_id: int, name: Optional[str]) -> None:
        """None goes back to the default."""
        await self._set_field(guild_id, "currency_name", name)

    # ---- muted role ----

    async def get_muted_role_id(self, guild_id: int) -> Optional[int]:
        return await self._get_field(guild_id, "muted_role_id")

    async def set_muted_role_id(self, guild_id: int, role_id: Optional[int]) -> None:
        await self._set_field(guild_id, "muted_role_id", role_id)

    # ---- protected roles ----

    async def get_protected_role_ids(self, guild_id: int) -> set[int]:
        return set(await self._get_field(guild_id, "protected_role_ids", []))

    async def set_protected_role_ids(self, guild_id: int, role_ids: set[int]) -> None:
        await self._set_field(guild_id, "protected_role_ids", sorted(role_ids))

    async def add_protected_role(self, guild_id: int, role_id: int) -> bool:
        """Returns False if the role was already protected (no-op)."""
        roles = await self.get_protected_role_ids(guild_id)
        if role_id in roles:
            return False
        roles.add(role_id)
        await self.set_protected_role_ids(guild_id, roles)
        return True

    async def remove_protected_role(self, guild_id: int, role_id: int) -> bool:
        """Returns False if the role wasn't protected (no-op)."""
        roles = await self.get_protected_role_ids(guild_id)
        if role_id not in roles:
            return False
        roles.discard(role_id)
        await self.set_protected_role_ids(guild_id, roles)
        return True

    # ---- punishment ladder ----

    async def get_punishment_ladder(self, guild_id: int) -> dict[int, Punishment]:
        raw_ladder = await self._get_field(guild_id, "punishment_ladder")
        if not raw_ladder:
            return DEFAULT_LADDER
        return {
            int(strikes): Punishment(
                action=PunishmentAction(entry["action"]),
                duration=entry.get("duration"),
                count=entry.get("count"),
            )
            for strikes, entry in raw_ladder.items()
        }

    async def set_punishment(self, guild_id: int, strikes: int, punishment: Punishment) -> None:
        ladder = dict(await self._get_field(guild_id, "punishment_ladder") or {})
        ladder[str(strikes)] = {
            "action": punishment.action.value,
            "duration": punishment.duration,
            "count": punishment.count,
        }
        await self._set_field(guild_id, "punishment_ladder", ladder)

    async def remove_punishment(self, guild_id: int, strikes: int) -> bool:
        """Returns False if there was no entry at that threshold (no-op)."""
        ladder = dict(await self._get_field(guild_id, "punishment_ladder") or {})
        if str(strikes) not in ladder:
            return False
        del ladder[str(strikes)]
        await self._set_field(guild_id, "punishment_ladder", ladder)
        return True

    # ---- automod thresholds ----

    async def get_caps_enabled(self, guild_id: int) -> bool:
        return bool(await self._get_field(guild_id, "caps_enabled", DEFAULT_CAPS_ENABLED))

    async def set_caps_enabled(self, guild_id: int, enabled: bool) -> None:
        await self._set_field(guild_id, "caps_enabled", enabled)

    async def get_anti_spam(self, guild_id: int) -> int:
        """Messages/minute threshold; -1 means disabled."""
        return await self._get_field(guild_id, "anti_spam", DEFAULT_ANTI_SPAM)

    async def set_anti_spam(self, guild_id: int, value: int) -> None:
        await self._set_field(guild_id, "anti_spam", value)

    async def get_similar_messages(self, guild_id: int) -> int:
        """Similar-message-count threshold; -1 means disabled."""
        return await self._get_field(guild_id, "similar_messages", DEFAULT_SIMILAR_MESSAGES)

    async def set_similar_messages(self, guild_id: int, value: int) -> None:
        await self._set_field(guild_id, "similar_messages", value)

    async def get_link_cooldown(self, guild_id: int) -> int:
        """Seconds between links from the same user; -1 means disabled."""
        return await self._get_field(guild_id, "link_cooldown", DEFAULT_LINK_COOLDOWN)

    async def set_link_cooldown(self, guild_id: int, seconds: int) -> None:
        await self._set_field(guild_id, "link_cooldown", seconds)

    async def get_attachment_cooldown(self, guild_id: int) -> int:
        """Seconds between attachments from the same user; -1 means disabled."""
        return await self._get_field(guild_id, "attachment_cooldown", DEFAULT_ATTACHMENT_COOLDOWN)

    async def set_attachment_cooldown(self, guild_id: int, seconds: int) -> None:
        await self._set_field(guild_id, "attachment_cooldown", seconds)

    # ---- invites ----

    async def get_invites_allowed(self, guild_id: int) -> bool:
        return bool(await self._get_field(guild_id, "invites_allowed", DEFAULT_INVITES_ALLOWED))

    async def set_invites_allowed(self, guild_id: int, allowed: bool) -> None:
        await self._set_field(guild_id, "invites_allowed", allowed)

    async def get_channel_invites_override(self, guild_id: int, channel_id: int) -> Optional[bool]:
        """None means the channel has no override and inherits the guild default."""
        overrides = await self._get_field(guild_id, "channel_invite_overrides", {})
        value = overrides.get(str(channel_id))
        return value if isinstance(value, bool) else None

    async def get_all_channel_invite_overrides(self, guild_id: int) -> dict[int, bool]:
        """Every channel-id -> override in this guild — for /export."""
        overrides = await self._get_field(guild_id, "channel_invite_overrides", {})
        return {int(cid): allowed for cid, allowed in overrides.items() if isinstance(allowed, bool)}

    async def set_channel_invites_override(
        self, guild_id: int, channel_id: int, allowed: Optional[bool]
    ) -> None:
        """allowed=None clears the override (channel inherits the guild default)."""
        overrides = dict(await self._get_field(guild_id, "channel_invite_overrides", {}))
        if allowed is None:
            overrides.pop(str(channel_id), None)
        else:
            overrides[str(channel_id)] = allowed
        await self._set_field(guild_id, "channel_invite_overrides", overrides)
