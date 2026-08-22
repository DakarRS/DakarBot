"""Message-filter enforcement — ports modbot's AutoModManager.js (run from
AutoModEventListener.js / AutoModMessageEditEventListener.js) plus
AutoResponseEventListener.js's separate auto-reply pass. Cloud Vision image scanning
is deliberately not ported — see filtering/models.py's module docstring.

Two deliberate behavior fixes versus the JS source, not faithful ports:

- modbot's bad-word check applies its own configured punishment (mute/ban/strike/
  none) AND the outer dispatcher loop still applies a blanket 30-second timeout on
  top, since the loop only knows "was the message deleted", not "did this check
  already punish the member itself". A bad-word configured to strike (or do nothing)
  would, in modbot, still always time the member out for 30s regardless. That's
  unintentional stacking, not an escalation, so it's fixed here: bad-words reports
  that it already handled punishment, and the dispatcher skips the generic timeout
  for it — every other check (caps, invites, cooldowns, spam/similar) still gets it.

- modbot's spam/similar-message check (`AutoModManager#spam`) calls
  `RepeatedMessage.checkSpam`/`checkSimilar`, which already deletes the offending
  messages and sends its own warning — then, on a true result, `#spam` calls
  `#deleteAndWarn` on the *same already-deleted message* again, sending a *second*,
  differently-worded warning and re-attempting a delete that's already happened
  (silently swallowed as an "unknown message" error). That's a redundant double-
  warning bug, not two intentionally different notices, so it's not ported: spam and
  similar-message checks here send exactly one warning, from tracking.py.
"""
from __future__ import annotations

import datetime
import logging
import random
import re
import urllib.parse
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

import discord

from ..guild_settings import GuildSettings
from ..moderation.duration import format_duration
from ..moderation.models import ModeratedMember, PunishmentAction
from . import models, tracking
from .models import BadWord

if TYPE_CHECKING:
    from ..bot import DakarBot
    from ..db import Database

logger = logging.getLogger("dakarbot.filtering")

GENERIC_TIMEOUT_SECONDS = 30
WARNING_DISPLAY_SECONDS = 5

INVITE_DOMAINS = (
    "discord.gg",
    "discord.com/invite",
    "discordapp.com/invite",
    "invite.gg",
    "discord.me",
    "top.gg/servers",
    "dsc.gg",
)
_LINK_RE = re.compile(r"https?://", re.IGNORECASE)


class CheckOutcome(Enum):
    NOT_TRIGGERED = "not_triggered"
    TRIGGERED = "triggered"  # apply the generic 30s timeout
    TRIGGERED_PUNISHED = "triggered_punished"  # own punishment already applied


def _bool_outcome(triggered: bool) -> CheckOutcome:
    return CheckOutcome.TRIGGERED if triggered else CheckOutcome.NOT_TRIGGERED


Check = Callable[["DakarBot", "Database", GuildSettings, discord.Message], Awaitable[CheckOutcome]]


async def check_message(bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message) -> None:
    if await _ignored_by_automod(guild_settings, message):
        return
    await _run_checks(bot, db, guild_settings, message, (*_CONTENT_CHECKS, *_COOLDOWN_CHECKS))


async def check_message_edit(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> None:
    if await _ignored_by_automod(guild_settings, message):
        return
    await _run_checks(bot, db, guild_settings, message, _CONTENT_CHECKS)


async def check_auto_response(db: "Database", message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    channel = message.channel
    if isinstance(channel, discord.Thread) and channel.parent is not None:
        channel = channel.parent

    responses = await models.get_auto_responses_for_channel(db, message.guild.id, channel.id)
    triggered = [r for r in responses if r.matches(message.content)]
    if not triggered:
        return

    response = random.choice(triggered)
    try:
        await message.reply(response.response[:2000])
    except discord.Forbidden:
        logger.warning(
            "Missing permissions to respond to message in channel %s of guild %s",
            message.channel.id,
            message.guild.id,
        )
    except discord.HTTPException:
        pass


async def _run_checks(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message, checks: tuple[Check, ...]
) -> None:
    for check in checks:
        outcome = await check(bot, db, guild_settings, message)
        if outcome is CheckOutcome.NOT_TRIGGERED:
            continue
        if outcome is CheckOutcome.TRIGGERED:
            await _apply_generic_timeout(message)
        return


async def _ignored_by_automod(guild_settings: GuildSettings, message: discord.Message) -> bool:
    if message.guild is None or message.is_system() or message.author.bot:
        return True
    if not isinstance(message.author, discord.Member):
        return True
    if message.author.guild_permissions.manage_messages:
        return True
    protected_role_ids = await guild_settings.get_protected_role_ids(message.guild.id)
    if protected_role_ids and any(role.id in protected_role_ids for role in message.author.roles):
        return True
    return False


async def _apply_generic_timeout(message: discord.Message) -> None:
    member = message.author
    if not isinstance(member, discord.Member) or member.timed_out:
        return
    try:
        until = discord.utils.utcnow() + datetime.timedelta(seconds=GENERIC_TIMEOUT_SECONDS)
        await member.timeout(until, reason="Automod")
    except discord.HTTPException:
        pass


async def _delete_and_warn(bot: "DakarBot", message: discord.Message, reason: str, warning_text: str) -> CheckOutcome:
    try:
        await bot.delete_message(message, reason=reason)
    except discord.Forbidden:
        logger.warning(
            "Missing permissions to delete message in channel %s of guild %s",
            message.channel.id,
            message.guild.id,
        )
        return CheckOutcome.TRIGGERED
    except discord.HTTPException:
        pass
    await tracking.send_warning(bot, message, warning_text, WARNING_DISPLAY_SECONDS)
    return CheckOutcome.TRIGGERED


# ---- content checks ----


async def _check_bad_words(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> CheckOutcome:
    channel = message.channel
    if isinstance(channel, discord.Thread) and channel.parent is not None:
        channel = channel.parent

    for word in await models.get_bad_words_for_channel(db, message.guild.id, channel.id):
        if word.matches(message.content):
            await _apply_bad_word(bot, db, guild_settings, message, word)
            return CheckOutcome.TRIGGERED_PUNISHED
    return CheckOutcome.NOT_TRIGGERED


async def _apply_bad_word(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message, word: BadWord
) -> None:
    reason = "Using forbidden words or phrases"
    comment = f"(Filter ID: {word.id})"

    try:
        await bot.delete_message(message, reason=f"{reason} {comment}")
    except discord.HTTPException:
        pass

    response = word.get_response()
    if response is not None:
        await tracking.send_warning(bot, message, response, WARNING_DISPLAY_SECONDS)

    if word.punishment.action is not PunishmentAction.NONE and isinstance(message.author, discord.Member):
        moderated = ModeratedMember(db, guild_settings, message.guild, message.author)
        await moderated.execute_punishment(word.punishment, reason, comment)

    if word.dm:
        try:
            await message.author.send(f"Your message in **{message.guild.name}** was removed: {word.dm}")
        except discord.HTTPException:
            pass


async def _check_caps(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> CheckOutcome:
    if not await guild_settings.get_caps_enabled(message.guild.id):
        return CheckOutcome.NOT_TRIGGERED

    uppercase = len(re.findall(r"[A-Z]", message.content))
    lowercase = len(re.findall(r"[a-z]", message.content))
    if uppercase <= 5 or (uppercase / (lowercase + uppercase) < 0.7):
        return CheckOutcome.NOT_TRIGGERED

    return await _delete_and_warn(bot, message, "Using too many capital letters", "Don't use that many capital letters!")


def _includes_invite(content: str) -> bool:
    for part in re.split(r"(\s|://)", content):
        try:
            part = urllib.parse.unquote(part)
        except ValueError:
            pass
        for domain in INVITE_DOMAINS:
            if re.search(re.escape(domain) + r"/\w+", part):
                return True
    return False


async def _check_invites(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> CheckOutcome:
    if not _includes_invite(message.content):
        return CheckOutcome.NOT_TRIGGERED

    override = await guild_settings.get_channel_invites_override(message.guild.id, message.channel.id)
    allowed = override if override is not None else await guild_settings.get_invites_allowed(message.guild.id)
    if allowed:
        return CheckOutcome.NOT_TRIGGERED

    return await _delete_and_warn(bot, message, "Sending invite links", "Invites are not allowed here!")


# ---- cooldown checks ----


async def _check_attachment_cooldown(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> CheckOutcome:
    if not message.attachments:
        return CheckOutcome.NOT_TRIGGERED

    cooldown = await guild_settings.get_attachment_cooldown(message.guild.id)
    if cooldown == -1:
        return CheckOutcome.NOT_TRIGGERED

    remaining = tracking.check_cooldown("attachment", message.guild.id, message.author.id, cooldown)
    if remaining is None:
        return CheckOutcome.NOT_TRIGGERED

    text = f"You can post an attachment again in {format_duration(remaining) or '1s'}!"
    return await _delete_and_warn(bot, message, "Sending attachments too quickly", text)


async def _check_link_cooldown(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> CheckOutcome:
    if not _LINK_RE.search(message.content):
        return CheckOutcome.NOT_TRIGGERED

    cooldown = await guild_settings.get_link_cooldown(message.guild.id)
    if cooldown == -1:
        return CheckOutcome.NOT_TRIGGERED

    remaining = tracking.check_cooldown("link", message.guild.id, message.author.id, cooldown)
    if remaining is None:
        return CheckOutcome.NOT_TRIGGERED

    text = f"You can post a link again in {format_duration(remaining) or '1s'}!"
    return await _delete_and_warn(bot, message, "Sending links too quickly", text)


async def _check_spam(
    bot: "DakarBot", db: "Database", guild_settings: GuildSettings, message: discord.Message
) -> CheckOutcome:
    anti_spam = await guild_settings.get_anti_spam(message.guild.id)
    similar_messages = await guild_settings.get_similar_messages(message.guild.id)
    if anti_spam == -1 and similar_messages == -1:
        return CheckOutcome.NOT_TRIGGERED

    tracking.add_message(message)
    if anti_spam != -1 and await tracking.check_spam(bot, message, anti_spam, WARNING_DISPLAY_SECONDS):
        return CheckOutcome.TRIGGERED
    if similar_messages != -1 and await tracking.check_similar(bot, message, similar_messages, WARNING_DISPLAY_SECONDS):
        return CheckOutcome.TRIGGERED
    return CheckOutcome.NOT_TRIGGERED


_CONTENT_CHECKS: tuple[Check, ...] = (_check_bad_words, _check_caps, _check_invites)
_COOLDOWN_CHECKS: tuple[Check, ...] = (_check_attachment_cooldown, _check_link_cooldown, _check_spam)
