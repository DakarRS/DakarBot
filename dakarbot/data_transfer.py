"""Guild data export/import — ports modbot's src/database/export/{Exporter,
ModBotImporter,VortexImporter}.js.

/export produces modbot's own "modbot-1.x" JSON format rather than inventing a
separate DakarBot-only one: this way DakarBot's own exports and a real modbot
guild's exports both import through the exact same code path below, and a guild
can move between the two bots in either direction. Fields DakarBot has no setting
for (playlist, helpcenter, safeSearch — see cogs/settings.py's own scope note on
those) are emitted with inert defaults on export and simply ignored on import.

Moderation/bad-word/auto-response rows always get a fresh id on import, regardless
of what id the source data carried — matching modbot's own ModBotImporter (which
constructs brand-new BadWord/AutoResponse instances without ever passing the
source's id through) and avoiding any risk of colliding with existing rows in the
target guild/instance.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

import discord

from .filtering import models as filtering_models
from .filtering.models import AutoResponse, BadWord, Trigger, TriggerType
from .moderation import models as moderation_models
from .moderation.models import Moderation, ModerationAction, Punishment, PunishmentAction

if TYPE_CHECKING:
    from .bot import DakarBot
    from .channel_settings import ChannelSettings
    from .db import Database
    from .guild_settings import GuildSettings

DATA_TYPE = "modbot-1.0.0"

# modbot's own punishment-ladder actions — a strike-punishment can't itself be
# "strike" (that would be a no-op/loop), matching settings.py's own ladder choices.
_VALID_PUNISHMENT_ACTIONS = {"ban", "kick", "mute", "softban", "strike"}

# JS's Number.MAX_SAFE_INTEGER — Vortex represents a permanent mute/ban with a
# sentinel at or above this rather than a real expiry timestamp.
_VORTEX_SENTINEL = 2**53 - 1


def _parse_id(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---- export ----


async def export_guild(bot: "DakarBot", guild_id: int) -> dict[str, Any]:
    return {
        "dataType": DATA_TYPE,
        "guildID": str(guild_id),
        "guildConfig": await _export_guild_config(bot.guild_settings, guild_id),
        "channels": await _export_channels(bot.guild_settings, bot.channel_settings, guild_id),
        "moderations": await _export_moderations(bot.db, guild_id),
        "responses": await _export_auto_responses(bot.db, guild_id),
        "badWords": await _export_bad_words(bot.db, guild_id),
    }


async def _export_guild_config(guild_settings: "GuildSettings", guild_id: int) -> dict[str, Any]:
    ladder = await guild_settings.get_punishment_ladder(guild_id)
    punishments = {
        str(strikes): {"action": p.action.value, "duration": p.duration, "count": p.count}
        for strikes, p in ladder.items()
    }
    muted_role_id = await guild_settings.get_muted_role_id(guild_id)
    log_channel_id = await guild_settings.get_log_channel_id(guild_id)
    message_log_channel_id = await guild_settings.get_message_log_channel_id(guild_id)
    join_log_channel_id = await guild_settings.get_join_log_channel_id(guild_id)

    return {
        "logChannel": str(log_channel_id) if log_channel_id else None,
        "messageLogChannel": str(message_log_channel_id) if message_log_channel_id else None,
        "joinLogChannel": str(join_log_channel_id) if join_log_channel_id else None,
        "mutedRole": str(muted_role_id) if muted_role_id else None,
        "playlist": None,
        "helpcenter": None,
        "invites": await guild_settings.get_invites_allowed(guild_id),
        "linkCooldown": await guild_settings.get_link_cooldown(guild_id),
        "attachmentCooldown": await guild_settings.get_attachment_cooldown(guild_id),
        "caps": await guild_settings.get_caps_enabled(guild_id),
        "antiSpam": await guild_settings.get_anti_spam(guild_id),
        "similarMessages": await guild_settings.get_similar_messages(guild_id),
        "safeSearch": {"enabled": False, "strikes": 0, "likelihood": 1},
        "protectedRoles": [str(r) for r in await guild_settings.get_protected_role_ids(guild_id)],
        "punishments": punishments,
    }


async def _export_channels(
    guild_settings: "GuildSettings", channel_settings: "ChannelSettings", guild_id: int
) -> list[dict[str, Any]]:
    overrides = await guild_settings.get_all_channel_invite_overrides(guild_id)
    locked_ids = set(await channel_settings.get_locked_channel_ids(guild_id))
    channel_ids = set(overrides) | locked_ids

    result = []
    for channel_id in sorted(channel_ids):
        lock = await channel_settings.get_lock(channel_id) if channel_id in locked_ids else {}
        result.append({"id": str(channel_id), "invites": overrides.get(channel_id), "lock": lock})
    return result


async def _export_moderations(db: "Database", guild_id: int) -> list[dict[str, Any]]:
    rows = await db.fetchall("SELECT * FROM moderations WHERE guild_id = $1", guild_id)
    return [
        {
            "id": row["id"],
            "guildid": str(row["guild_id"]),
            "userid": str(row["user_id"]),
            "action": row["action"],
            "created": row["created"],
            "value": row["value"],
            "reason": row["reason"],
            "comment": row["comment"],
            "expireTime": row["expire_time"],
            "moderator": str(row["moderator"]) if row["moderator"] else None,
            "active": row["active"],
        }
        for row in rows
    ]


def _export_trigger(trigger: Trigger) -> dict[str, Any]:
    return {"type": trigger.type.value, "content": trigger.content, "flags": trigger.flags}


async def _export_auto_responses(db: "Database", guild_id: int) -> list[dict[str, Any]]:
    responses = await filtering_models.get_all_auto_responses(db, guild_id)
    return [
        {
            "id": r.id,
            "trigger": _export_trigger(r.trigger),
            "response": r.response,
            "global": r.is_global,
            "channels": [str(c) for c in r.channel_ids],
        }
        for r in responses
    ]


async def _export_bad_words(db: "Database", guild_id: int) -> list[dict[str, Any]]:
    words = await filtering_models.get_all_bad_words(db, guild_id)
    return [
        {
            "id": w.id,
            "trigger": _export_trigger(w.trigger),
            "response": w.response,
            "global": w.is_global,
            "channels": [str(c) for c in w.channel_ids],
            "punishment": {
                "action": w.punishment.action.value,
                "duration": w.punishment.duration,
                "count": w.punishment.count,
            },
            "priority": w.priority,
            "dm": w.dm,
        }
        for w in words
    ]


# ---- format detection ----


def detect_format(data: Any) -> Optional[str]:
    """Returns "modbot", "vortex", or None (unrecognized) — matches
    ImportCommand.getImporter's dataType sniffing."""
    if not isinstance(data, dict):
        return None
    data_type = data.get("dataType")
    if not data_type:
        if all(key in data for key in ("tempmutes", "strikes", "tempbans")):
            return "vortex"
        return None
    if isinstance(data_type, str) and data_type.lower().startswith("modbot-1."):
        return "modbot"
    return None


@dataclass
class ImportResult:
    counts: dict[str, int] = field(default_factory=dict)


# ---- modbot import ----


async def import_modbot_data(bot: "DakarBot", guild: discord.Guild, data: dict[str, Any]) -> ImportResult:
    await _import_guild_config(bot.guild_settings, guild, data.get("guildConfig") or {})

    channels = data.get("channels") or []
    await _import_channels(bot.guild_settings, bot.channel_settings, guild, channels)

    moderations = data.get("moderations") or []
    imported_moderations = await _import_moderations(bot.db, guild.id, moderations)

    responses = data.get("responses") or []
    imported_responses = await _import_auto_responses(bot.db, guild.id, responses)

    bad_words = data.get("badWords") or []
    imported_bad_words = await _import_bad_words(bot.db, guild.id, bad_words)

    return ImportResult(
        counts={
            "Channel Configs": len(channels),
            "Moderations": imported_moderations,
            "Responses": imported_responses,
            "Bad Words": imported_bad_words,
        }
    )


async def _import_guild_config(guild_settings: "GuildSettings", guild: discord.Guild, config: dict[str, Any]) -> None:
    for key, setter in (
        ("logChannel", guild_settings.set_log_channel_id),
        ("messageLogChannel", guild_settings.set_message_log_channel_id),
        ("joinLogChannel", guild_settings.set_join_log_channel_id),
    ):
        channel_id = _parse_id(config.get(key))
        if channel_id is not None and guild.get_channel(channel_id) is None:
            channel_id = None
        await setter(guild.id, channel_id)

    muted_role_id = _parse_id(config.get("mutedRole"))
    if muted_role_id is not None and guild.get_role(muted_role_id) is None:
        muted_role_id = None
    await guild_settings.set_muted_role_id(guild.id, muted_role_id)

    protected_ids = {
        role_id
        for raw_id in (config.get("protectedRoles") or [])
        if (role_id := _parse_id(raw_id)) is not None and guild.get_role(role_id) is not None
    }
    await guild_settings.set_protected_role_ids(guild.id, protected_ids)

    if isinstance(config.get("invites"), bool):
        await guild_settings.set_invites_allowed(guild.id, config["invites"])
    if isinstance(config.get("linkCooldown"), (int, float)):
        await guild_settings.set_link_cooldown(guild.id, int(config["linkCooldown"]))
    if isinstance(config.get("attachmentCooldown"), (int, float)):
        await guild_settings.set_attachment_cooldown(guild.id, int(config["attachmentCooldown"]))
    if isinstance(config.get("caps"), bool):
        await guild_settings.set_caps_enabled(guild.id, config["caps"])
    if isinstance(config.get("antiSpam"), (int, float)):
        await guild_settings.set_anti_spam(guild.id, int(config["antiSpam"]))
    if isinstance(config.get("similarMessages"), (int, float)):
        await guild_settings.set_similar_messages(guild.id, int(config["similarMessages"]))

    for strikes_str, entry in (config.get("punishments") or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            strikes = int(strikes_str)
            action = PunishmentAction(entry.get("action"))
        except ValueError:
            continue
        if action.value not in _VALID_PUNISHMENT_ACTIONS:
            continue
        punishment = Punishment(action=action, duration=entry.get("duration"), count=entry.get("count"))
        await guild_settings.set_punishment(guild.id, strikes, punishment)


async def _import_channels(
    guild_settings: "GuildSettings", channel_settings: "ChannelSettings", guild: discord.Guild, channels: list[Any]
) -> None:
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        channel_id = _parse_id(entry.get("id"))
        if channel_id is None or guild.get_channel(channel_id) is None:
            continue
        invites = entry.get("invites")
        if isinstance(invites, bool):
            await guild_settings.set_channel_invites_override(guild.id, channel_id, invites)
        lock = entry.get("lock")
        if isinstance(lock, dict) and lock:
            await channel_settings.set_lock(guild.id, channel_id, lock)


async def _import_moderations(db: "Database", guild_id: int, moderations: list[Any]) -> int:
    count = 0
    for entry in moderations:
        if not isinstance(entry, dict):
            continue
        try:
            action = ModerationAction(entry.get("action"))
        except ValueError:
            continue
        user_id = _parse_id(entry.get("userid"))
        if user_id is None:
            continue
        await moderation_models.create(
            db,
            Moderation(
                id=None,
                guild_id=guild_id,
                user_id=user_id,
                action=action,
                created=int(entry.get("created") or time.time()),
                value=int(entry.get("value") or 0),
                expire_time=_parse_id(entry.get("expireTime")),
                reason=entry.get("reason") or "No reason provided.",
                comment=entry.get("comment"),
                moderator=_parse_id(entry.get("moderator")),
                active=bool(entry.get("active", True)),
            ),
        )
        count += 1
    return count


def _import_trigger(raw: Any) -> Optional[Trigger]:
    if not isinstance(raw, dict):
        return None
    try:
        return Trigger(type=TriggerType(raw.get("type")), content=raw["content"], flags=raw.get("flags"))
    except (KeyError, ValueError):
        return None


def _import_channel_ids(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    return [cid for c in raw if (cid := _parse_id(c)) is not None]


async def _import_auto_responses(db: "Database", guild_id: int, responses: list[Any]) -> int:
    count = 0
    for entry in responses:
        if not isinstance(entry, dict):
            continue
        trigger = _import_trigger(entry.get("trigger"))
        if trigger is None:
            continue
        await filtering_models.create_auto_response(
            db,
            AutoResponse(
                id=None,
                guild_id=guild_id,
                trigger=trigger,
                response=entry.get("response") or "",
                is_global=bool(entry.get("global")),
                channel_ids=_import_channel_ids(entry.get("channels")),
            ),
        )
        count += 1
    return count


async def _import_bad_words(db: "Database", guild_id: int, bad_words: list[Any]) -> int:
    count = 0
    for entry in bad_words:
        if not isinstance(entry, dict):
            continue
        trigger = _import_trigger(entry.get("trigger"))
        if trigger is None:
            continue
        punishment_raw = entry.get("punishment") or {}
        try:
            action = PunishmentAction(punishment_raw.get("action", "none"))
        except ValueError:
            action = PunishmentAction.NONE
        punishment = Punishment(
            action=action, duration=punishment_raw.get("duration"), count=punishment_raw.get("count")
        )
        await filtering_models.create_bad_word(
            db,
            BadWord(
                id=None,
                guild_id=guild_id,
                trigger=trigger,
                punishment=punishment,
                response=entry.get("response") or "disabled",
                dm=entry.get("dm"),
                is_global=bool(entry.get("global")),
                channel_ids=_import_channel_ids(entry.get("channels")),
                priority=int(entry.get("priority") or 0),
            ),
        )
        count += 1
    return count


# ---- vortex import ----


async def import_vortex_data(bot: "DakarBot", guild: discord.Guild, data: dict[str, Any]) -> ImportResult:
    moderator_id = bot.user.id if bot.user else None

    mute_count = await _import_vortex_timed(
        bot.db, guild.id, data.get("tempmutes") or {}, ModerationAction.MUTE, moderator_id
    )
    ban_count = await _import_vortex_timed(
        bot.db, guild.id, data.get("tempbans") or {}, ModerationAction.BAN, moderator_id
    )
    strike_count = await _import_vortex_strikes(bot.db, guild.id, data.get("strikes") or {}, moderator_id)

    return ImportResult(counts={"Mutes": mute_count, "Strikes": strike_count, "Bans": ban_count})


async def _import_vortex_timed(
    db: "Database", guild_id: int, entries: dict[str, Any], action: ModerationAction, moderator_id: Optional[int]
) -> int:
    count = 0
    for user_id_raw, value in entries.items():
        if not isinstance(value, (int, float)) or value >= _VORTEX_SENTINEL:
            continue
        user_id = _parse_id(user_id_raw)
        if user_id is None:
            continue
        await moderation_models.create(
            db,
            Moderation(
                id=None,
                guild_id=guild_id,
                user_id=user_id,
                action=action,
                created=int(time.time()),
                expire_time=int(value),
                reason="Imported from Vortex",
                moderator=moderator_id,
            ),
        )
        count += 1
    return count


async def _import_vortex_strikes(
    db: "Database", guild_id: int, entries: dict[str, Any], moderator_id: Optional[int]
) -> int:
    count = 0
    for user_id_raw, value in entries.items():
        if not isinstance(value, (int, float)):
            continue
        user_id = _parse_id(user_id_raw)
        if user_id is None:
            continue
        await moderation_models.create(
            db,
            Moderation(
                id=None,
                guild_id=guild_id,
                user_id=user_id,
                action=ModerationAction.STRIKE,
                created=int(time.time()),
                value=int(value),
                reason="Imported from Vortex",
                moderator=moderator_id,
            ),
        )
        count += 1
    return count
