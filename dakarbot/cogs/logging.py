"""Logging cog: join/leave logging, message edit/delete logging, and detecting
manual moderator actions performed outside the bot via the audit log.

Ports modbot's LogJoinEventListener, GuildMemberRemoveEventListener,
LogMessageUpdateEventListener, MessageDeleteEventListener,
MessageDeleteBulkEventListener, and GuildAuditLogCreateEventListener.

Log channel routing (confirmed against modbot's GuildWrapper.js — three distinct
settings, not one shared channel):
- join_log_channel_id: member join AND leave (modbot has no separate leave-log
  setting; both use the same channel)
- message_log_channel_id: message edit, message delete (single + bulk)
- log_channel_id: audit-log-detected moderations (ban/unban/kick/mute/unmute) and
  member-prune notices — same channel every bot-issued moderation logs to, via
  ModeratedMember._log_case

Deliberate deviation from modbot: a manual unban done directly in Discord (not
through the bot) fires both a gateway member_unban event and an audit-log `unban`
entry in modbot, producing two separate log messages for the same action. Here,
the gateway-based listener (on_member_unban, in the moderation cog) stays a silent
DB-sync safety net only — it never posts to the log channel. This cog's audit-log
handler is the sole source of the attributed case log, so a manual unban is
logged exactly once instead of twice.
"""

import logging
import re
import time
from difflib import SequenceMatcher

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..ui import Icon, LayoutView
from ..moderation.models import ModeratedMember, ModerationAction, ModerationTarget

logger = logging.getLogger("dakarbot.logging")

# Whitespace runs, word runs, and individual punctuation characters each become their
# own token. Splitting only on whitespace (the original approach) made "dude" and
# "dude," count as two entirely different tokens whenever a word picked up trailing
# punctuation, so SequenceMatcher would see no match at all and mark the *whole* word
# plus everything after it as replaced — observed live: appending ", log my deletions
# already" after "dude" showed "dude" itself as struck-through-and-re-added instead of
# staying plain, since "dude" was never edited at all.
_TOKEN_RE = re.compile(r"\s+|\w+|[^\s\w]")
_DIFF_LIMIT = 4096
_BULK_DELETE_DESCRIPTION_LIMIT = 4000
_BULK_DELETE_FILE_LIMIT = 10


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _escape_diff_markers(text: str) -> str:
    return text.replace("~~", "\\~\\~").replace("__", "\\_\\_")


def _closes_style_marker(part: str) -> bool:
    return part.endswith("~~") or part.endswith("__")


def _opens_style_marker(part: str) -> bool:
    return part.startswith("~~") or part.startswith("__")


def word_diff(old: str, new: str, limit: int = _DIFF_LIMIT) -> str:
    """Word-level diff: added text underlined, removed text struck through,
    unchanged text plain — mirrors modbot's use of the JS `diff` package's
    diffWords(), truncated at a part boundary rather than mid-string so markdown
    markers never get cut in half."""
    old_tokens = _tokenize(old)
    new_tokens = _tokenize(new)
    matcher = SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)

    parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(_escape_diff_markers("".join(old_tokens[i1:i2])))
            continue
        if tag in ("delete", "replace"):
            removed = _escape_diff_markers("".join(old_tokens[i1:i2]))
            if removed.strip():
                parts.append(f"~~{removed}~~")
        if tag in ("insert", "replace"):
            added = _escape_diff_markers("".join(new_tokens[j1:j2]))
            if added.strip():
                parts.append(f"__{added}__")

    # Discord's markdown parser can fail to close a ~~...~~ or __...__ span when its
    # closing marker directly touches another span's opening marker with no
    # whitespace between them (observed live: "~~see~~__seeeee__" rendered the
    # strikethrough as literal tildes while the adjacent underline still worked).
    # A replace opcode is exactly where this happens — the removed and added text
    # sit at the same position with no original whitespace between them — so force
    # a single space at any such boundary.
    spaced_parts = []
    for part in parts:
        if spaced_parts and _closes_style_marker(spaced_parts[-1]) and _opens_style_marker(part):
            spaced_parts.append(" ")
        spaced_parts.append(part)

    result = []
    total = 0
    for part in spaced_parts:
        if total + len(part) > limit:
            result.append("...")
            break
        result.append(part)
        total += len(part)
    return "".join(result)


class LoggingCog(DakarCog, name="Logging"):
    async def _send_to(
        self, guild: discord.Guild, channel_id, container: discord.ui.Container, **kwargs
    ) -> None:
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.send(view=LayoutView(container), **kwargs)
        except discord.HTTPException:
            logger.warning("Failed to post log message to channel %s in guild %s", channel_id, guild.id)

    @staticmethod
    def _user_section(avatar_url: str, *texts: str) -> discord.ui.Section:
        """A Section pairing an avatar thumbnail with one or two lines of text —
        the shape every join/leave/edit/delete listener below needs."""
        return discord.ui.Section(
            *(discord.ui.TextDisplay(t) for t in texts), accessory=discord.ui.Thumbnail(url=avatar_url)
        )

    @staticmethod
    def _subtext(text: str) -> discord.ui.TextDisplay:
        return discord.ui.TextDisplay(f"-# {text}")

    # ---- join / leave ----

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        channel_id = await self.guild_settings.get_join_log_channel_id(member.guild.id)
        details = f"User ID: {member.id}\nCreated Account: {discord.utils.format_dt(member.created_at, 'R')}"
        section = self._user_section(
            member.display_avatar.url, f"📥 **{member.display_name} joined this server**", details
        )
        container = discord.ui.Container(
            section, self._subtext(f"👥 Members: {member.guild.member_count}"), color=discord.Color.green()
        )
        await self._send_to(member.guild, channel_id, container)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        channel_id = await self.guild_settings.get_join_log_channel_id(member.guild.id)
        details = f"User ID: {member.id}\nCreated Account: {discord.utils.format_dt(member.created_at, 'R')}"
        if member.joined_at:
            details += f"\nJoined: {discord.utils.format_dt(member.joined_at, 'R')}"
        section = self._user_section(
            member.display_avatar.url, f"📤 **{member.display_name} left this server**", details
        )
        container = discord.ui.Container(
            section, self._subtext(f"👥 Members: {member.guild.member_count}"), color=discord.Color.red()
        )
        await self._send_to(member.guild, channel_id, container)

    # ---- message edit ----

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        # pycord only dispatches this event at all when the pre-edit message was
        # cached (has real content) — an uncached/partial edit never reaches here,
        # which is the same "skip if oldMessage.content is falsy" guard modbot has,
        # just enforced one layer up by the library instead of in this listener.
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        channel_id = await self.guild_settings.get_message_log_channel_id(before.guild.id)
        if not channel_id:
            return

        section = self._user_section(
            before.author.display_avatar.url,
            f"✏️ **Message by {before.author.display_name} in #{before.channel.name} was edited**",
            word_diff(before.content, after.content),
        )
        container = discord.ui.Container(
            section, self._subtext(f"{Icon.ID} User ID: {before.author.id}"), color=discord.Color.orange()
        )
        await self._send_to(before.guild, channel_id, container)

    # ---- message delete (single) ----

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if message.id in self.bot.recently_deleted_message_ids:
            self.bot.recently_deleted_message_ids.discard(message.id)
            return

        channel_id = await self.guild_settings.get_message_log_channel_id(message.guild.id)
        if not channel_id:
            return

        if message.type is discord.MessageType.default:
            texts = [f"🗑️ **Message by {message.author.display_name} in #{message.channel.name} was deleted**"]
            if message.content:
                texts.append(message.content[:4096])
            items = [self._user_section(message.author.display_avatar.url, *texts)]
        else:
            items = [discord.ui.TextDisplay("🗑️ **A system message was deleted**")]

        files, missing = await self._attachments_to_files(message.attachments)
        footer = f"{Icon.ID} User ID: {message.author.id}"
        if missing:
            footer += f" | Missing Attachments: {missing}"
        items.append(self._subtext(footer))

        container = discord.ui.Container(*items, color=discord.Color.red())
        await self._send_to(message.guild, channel_id, container, files=files)

    # ---- message delete (automod) ----

    @commands.Cog.listener()
    async def on_automod_delete(self, message: discord.Message, reason: str) -> None:
        """Fired by DakarBot.delete_message(message, reason=...) — the filtering
        cog's checks all delete through that helper with a reason, so a message
        removed by automod still gets a log entry (attributed to that reason)
        instead of on_message_delete's skip-if-bot-deleted guard silently dropping
        it. Matches modbot's Bot.delete(message, reason) also calling
        logMessageDeletion, rather than a bot-initiated delete being unlogged."""
        if message.guild is None:
            return
        channel_id = await self.guild_settings.get_message_log_channel_id(message.guild.id)
        if not channel_id:
            return

        texts = [f"{Icon.WARNING} **Message by {message.author.display_name} in #{message.channel.name} was deleted**"]
        if message.content:
            texts.append(message.content[:4096])
        items = [self._user_section(message.author.display_avatar.url, *texts)]

        files, missing = await self._attachments_to_files(message.attachments)
        footer = f"📋 Reason: {reason} | {Icon.ID} User ID: {message.author.id}"
        if missing:
            footer += f" | Missing Attachments: {missing}"
        items.append(self._subtext(footer))

        container = discord.ui.Container(*items, color=discord.Color.red())
        await self._send_to(message.guild, channel_id, container, files=files)

    @staticmethod
    async def _attachments_to_files(
        attachments: list[discord.Attachment], limit: int = _BULK_DELETE_FILE_LIMIT
    ) -> tuple[list[discord.File], int]:
        files: list[discord.File] = []
        missing = 0
        for attachment in attachments:
            if len(files) >= limit:
                break
            try:
                files.append(await attachment.to_file(spoiler=True))
            except discord.HTTPException:
                missing += 1
        return files, missing

    # ---- message delete (bulk) ----

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        # Unlike single-delete, modbot never skips bot-initiated bulk deletes here —
        # ported as-is, so e.g. /strike-purge's own bulk delete still gets logged.
        if not messages or messages[0].guild is None:
            return
        guild = messages[0].guild
        channel_id = await self.guild_settings.get_message_log_channel_id(guild.id)
        if not channel_id:
            return

        lines = []
        total_len = 0
        truncated = False
        for msg in sorted(messages, key=lambda m: m.created_at):
            if not msg.content and not msg.attachments:
                continue
            attachments_note = f" [{len(msg.attachments)} files]" if msg.attachments else ""
            line = f"{msg.author.mention} ({msg.id}){attachments_note}: {msg.content}"
            if total_len + len(line) > _BULK_DELETE_DESCRIPTION_LIMIT:
                truncated = True
                break
            lines.append(line)
            total_len += len(line)

        if not lines:
            return

        channel = messages[0].channel
        items = [
            discord.ui.TextDisplay(f"🗑️ **{len(messages)} messages were deleted in #{channel.name}**"),
            discord.ui.TextDisplay("\n".join(lines)),
        ]
        if truncated:
            items.append(self._subtext("✂️ Some messages were omitted for length."))
        container = discord.ui.Container(*items, color=discord.Color.red())

        all_attachments = [a for msg in messages for a in msg.attachments]
        files, _missing = await self._attachments_to_files(all_attachments)

        await self._send_to(guild, channel_id, container, files=files)

    # ---- audit log: manual moderation done outside the bot ----

    @commands.Cog.listener()
    async def on_audit_log_entry(self, entry: discord.AuditLogEntry) -> None:
        # pycord only dispatches this when the executor is in the bot's user cache
        # (a real library constraint, not something this port can work around) —
        # with the members intent already enabled, that's true for any executor who
        # is currently a guild member, which covers the overwhelming majority of
        # real moderator actions.
        if entry.user is None or entry.user.bot:
            return

        handler = {
            discord.AuditLogAction.ban: self._handle_ban,
            discord.AuditLogAction.unban: self._handle_unban,
            discord.AuditLogAction.kick: self._handle_kick,
            discord.AuditLogAction.member_role_update: self._handle_role_update,
            discord.AuditLogAction.member_update: self._handle_member_update,
            discord.AuditLogAction.member_prune: self._handle_prune,
        }.get(entry.action)
        if handler is not None:
            await handler(entry.guild, entry)

    async def _record(
        self, guild: discord.Guild, entry: discord.AuditLogEntry, action: ModerationAction, **extra
    ) -> None:
        """Records an action the audit log shows already happened, attributed to its
        real executor — shared by every audit-log handler below."""
        target: ModerationTarget = entry.target
        moderated = ModeratedMember(self.db, self.guild_settings, guild, target)
        await moderated.record(
            action, entry.reason or "No reason provided.", "Executed through Discord", entry.user.id, **extra
        )

    async def _handle_ban(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> None:
        await self._record(guild, entry, ModerationAction.BAN)

    async def _handle_unban(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> None:
        await self._record(guild, entry, ModerationAction.UNBAN)

    async def _handle_kick(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> None:
        await self._record(guild, entry, ModerationAction.KICK)

    async def _handle_role_update(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> None:
        muted_role_id = await self.guild_settings.get_muted_role_id(guild.id)
        if muted_role_id is None:
            return

        # For member_role_update, pycord's audit-log diff repurposes before/after:
        # after.roles = roles added, before.roles = roles removed (confirmed against
        # pycord's own AuditLogChanges._handle_role implementation) — not the full
        # role set before/after the change.
        added_ids = {r.id for r in (getattr(entry.after, "roles", None) or [])}
        removed_ids = {r.id for r in (getattr(entry.before, "roles", None) or [])}

        if muted_role_id in added_ids and muted_role_id not in removed_ids:
            await self._record(guild, entry, ModerationAction.MUTE)
        elif muted_role_id in removed_ids and muted_role_id not in added_ids:
            await self._record(guild, entry, ModerationAction.UNMUTE)

    async def _handle_member_update(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> None:
        before_until = getattr(entry.before, "communication_disabled_until", None)
        after_until = getattr(entry.after, "communication_disabled_until", None)
        if before_until == after_until:
            return  # this member_update was about something else (nick, etc.)

        now = discord.utils.utcnow()
        if after_until is not None and after_until > now:
            duration = int((after_until - now).total_seconds())
            await self._record(guild, entry, ModerationAction.MUTE, expire_time=int(time.time()) + duration)
        elif before_until is not None and (after_until is None or after_until <= now):
            await self._record(guild, entry, ModerationAction.UNMUTE)

    async def _handle_prune(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> None:
        channel_id = await self.guild_settings.get_log_channel_id(guild.id)
        if not channel_id:
            return
        container = discord.ui.Container(
            discord.ui.TextDisplay("🧹 **Members Pruned**"),
            discord.ui.TextDisplay(
                f"📅 Days: {entry.extra.delete_member_days}\n"
                f"👋 Removed: {entry.extra.members_removed}\n"
                f"👥 New member count: {guild.member_count}"
            ),
            color=discord.Color.red(),
        )
        await self._send_to(guild, channel_id, container)


def setup(bot: DakarBot) -> None:
    bot.add_cog(LoggingCog(bot))
