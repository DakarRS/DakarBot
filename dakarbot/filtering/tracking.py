"""In-memory, per-process state for the message-filtering checks: spam/similar-
message detection (modbot's RepeatedMessage.js) and link/attachment cooldowns
(AutoModManager's `#cooldowns` map). Unlike filtering/models.py, none of this is
persisted — a bot restart clears it, matching modbot's own plain in-memory Maps.

Automod-triggered deletions still get logged to the message-log channel, attributed
to their specific reason (matching Bot.js's delete(message, reason) also calling
logMessageDeletion) — via DakarBot.delete_message(message, reason=...) dispatching
on_automod_delete, which the logging cog listens for. That keeps the reason-
attribution logic (and its Discord-formatting helpers) solely in the logging cog
rather than importing it here, while still avoiding on_message_delete's generic
"message vanished" notice for the same deletion (it's suppressed there via the
same recently_deleted_message_ids marking every bot-initiated delete already used).

One thing deliberately not reproduced from the JS source — a scope decision, not a
faithful port:

- modbot's own cooldown cleanup (`AutoModManager#cleanUpCaches`, on a 5s interval)
  stores each key's *last allowed action* timestamp and evicts once `stored_time <
  now` — which, running every 5 seconds, evicts almost every entry within 5 seconds
  of it being set, regardless of the actual configured cooldown length. That silently
  neuters any cooldown longer than ~5 seconds (the vast majority of configured
  values, since the default is 10s) — a real bug, not a design choice, so it's not
  ported. `check_cooldown` below stores each key's cooldown *end* time directly and
  `cleanup_cooldowns` only evicts once that end time has actually passed.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import discord

from ..bot import DakarBot
from .models import compare_two_strings

# How long a message stays eligible for spam/similar-message comparisons — matches
# RepeatedMessage.js's per-message 60000ms cache lifetime.
MESSAGE_RETENTION_SECONDS = 60

# Dice-coefficient threshold for two messages counting as "the same" — matches
# RepeatedMessage.js's similarEnough().
SIMILARITY_THRESHOLD = 0.85


# ---- spam / similar-message tracking ----


@dataclass
class _TrackedMessage:
    message: discord.Message
    added_at: float


@dataclass
class _UserTracker:
    entries: list[_TrackedMessage] = field(default_factory=list)
    warned: bool = False


_spam_trackers: dict[str, _UserTracker] = {}


def _spam_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}-{user_id}"


def _prune(tracker: _UserTracker, now: float) -> None:
    while tracker.entries and now - tracker.entries[0].added_at >= MESSAGE_RETENTION_SECONDS:
        tracker.entries.pop(0)


def add_message(message: discord.Message) -> None:
    """Track a message for spam/similar-message purposes. Must be called before
    check_spam/check_similar for that same message — both checks include the just-
    added message in their own counts, matching RepeatedMessage.add() being called
    before AutoModManager#spam runs its checks."""
    key = _spam_key(message.guild.id, message.author.id)
    now = time.monotonic()
    tracker = _spam_trackers.setdefault(key, _UserTracker())
    _prune(tracker, now)
    tracker.entries.append(_TrackedMessage(message=message, added_at=now))


def _similar_enough(a: discord.Message, b: discord.Message) -> bool:
    return compare_two_strings(a.content, b.content) > SIMILARITY_THRESHOLD


async def _bulk_delete(bot: DakarBot, messages: list[discord.Message], reason: str) -> None:
    if not messages:
        return
    bot.recently_deleted_message_ids.update(m.id for m in messages)
    try:
        await messages[0].channel.delete_messages(messages)
    except discord.HTTPException:
        pass
    # delete_messages() is a single bulk HTTP call, not per-message like
    # bot.delete_message(), so the on_automod_delete dispatch (for the message-log's
    # reason-attributed entry — see bot.py's delete_message docstring) happens here
    # instead, once per deleted message, matching RepeatedMessage.delete()'s own
    # per-message bot.logMessageDeletion(m, reason) calls.
    for m in messages:
        bot.dispatch("automod_delete", m, reason)


async def check_spam(bot: DakarBot, message: discord.Message, limit: int, timeout_seconds: float) -> bool:
    """Deletes every currently-tracked message for this user (in this guild) if more
    than `limit` are cached, warning them once per tracking window. Returns whether
    the message was (part of a) deletion."""
    key = _spam_key(message.guild.id, message.author.id)
    tracker = _spam_trackers.get(key)
    if tracker is None or len(tracker.entries) <= limit:
        return False

    await _bulk_delete(bot, [entry.message for entry in tracker.entries], "Sending messages too quickly")
    if not tracker.warned:
        tracker.warned = True
        await send_warning(bot, message, "Stop sending messages this fast!", timeout_seconds)
    return True


async def check_similar(bot: DakarBot, message: discord.Message, limit: int, timeout_seconds: float) -> bool:
    """Deletes every message in the tracking window that's similar enough to this one
    if that count exceeds `limit`, warning once per tracking window."""
    key = _spam_key(message.guild.id, message.author.id)
    tracker = _spam_trackers.get(key)
    if tracker is None:
        return False

    similar = [entry.message for entry in tracker.entries if _similar_enough(message, entry.message)]
    if len(similar) <= limit:
        return False

    await _bulk_delete(bot, similar, "Repeating messages")
    if not tracker.warned:
        tracker.warned = True
        await send_warning(bot, message, "Stop repeating your messages!", timeout_seconds)
    return True


async def send_warning(bot: DakarBot, message: discord.Message, text: str, timeout_seconds: float) -> None:
    """Sends a self-deleting mention+warning in the same channel — shared by the
    spam/similar-message checks here and the content checks in checks.py."""
    try:
        response = await message.channel.send(f"{message.author.mention} {text}")
    except discord.HTTPException:
        return
    bot.loop.create_task(_delayed_delete(bot, response, timeout_seconds))


async def _delayed_delete(bot: DakarBot, message: discord.Message, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(message)
    except discord.HTTPException:
        pass


# ---- link / attachment cooldowns ----

_cooldown_ends: dict[str, int] = {}


def _cooldown_key(kind: str, guild_id: int, user_id: int) -> str:
    return f"{kind}-{guild_id}-{user_id}"


def check_cooldown(kind: str, guild_id: int, user_id: int, cooldown_seconds: int) -> "int | None":
    """Returns None (and starts a fresh cooldown window) if the action is currently
    allowed, or the number of seconds remaining if it's still on cooldown."""
    key = _cooldown_key(kind, guild_id, user_id)
    now = int(time.time())
    cooldown_end = _cooldown_ends.get(key, 0)
    if cooldown_end <= now:
        _cooldown_ends[key] = now + cooldown_seconds
        return None
    return cooldown_end - now


def cleanup_stale() -> None:
    """Bounds memory for both caches above — call periodically (matches
    AutoModManager's own 5-second interval sweep, though nothing here strictly
    depends on running that often)."""
    now_ts = int(time.time())
    stale_cooldowns = [k for k, end in _cooldown_ends.items() if end <= now_ts]
    for key in stale_cooldowns:
        del _cooldown_ends[key]

    now_mono = time.monotonic()
    empty_trackers = []
    for key, tracker in _spam_trackers.items():
        _prune(tracker, now_mono)
        if not tracker.entries:
            empty_trackers.append(key)
    for key in empty_trackers:
        del _spam_trackers[key]
