"""Guild-utility cog: /server, /id, /role, /purge, /lock, /unlock. Ports modbot's
src/commands/guild/{GuildInfo,ID,RoleInfo,Purge,Lock,Unlock,BaseLock}Command.js
(PurgeInvitesCommand lands separately — it's gated behind a bot-operator feature
whitelist that doesn't exist in this port, same reasoning as settings.py's
safe-search/help-center/playlist scope note).

Lock/Unlock simplifications versus modbot, both enabled by pycord/Discord
capabilities modbot's JS didn't have available when it was written:
- The channel picker uses Discord's native channel-select component
  (discord.ui.ChannelSelect, filtered to lockable channel types), not a curated
  StringSelect built from a pre-fetched channel list. That drops modbot's 25-channel
  cap on the picker itself (Discord's native select isn't limited to 25 pre-built
  options) at the cost of not being able to pre-filter to *only* currently-lockable
  (or currently-locked, for unlock) channels the way the old approach could — so
  _lock_channel/_unlock_channel below silently no-op per-channel if there's nothing
  to actually do, rather than the picker only ever offering valid choices.
- Both single-shared BaseLockCommand.js's abstract-class hierarchy (getStrings/
  getChannels/performAction/getChannelMessageEmbed) collapses to two plain functions
  (_lock_channel/_unlock_channel) plus one shared modal class — there's no second
  concrete subclass coming later the way modbot's own command tree might imply.
- The "only post the lock/unlock notice if @everyone could actually see/post in the
  channel" check modbot does is dropped — the notice is always attempted (and
  silently skipped on HTTPException), which is simpler and low-stakes (worst case:
  one extra unseen message in an edge-case channel).

Deliberately NOT using `from __future__ import annotations`: see moderation/base.py's
module docstring for why (pycord reads discord.Option(...) off raw annotations).
"""
import datetime
import re
from typing import Optional

import discord

from ..bot import DakarBot, DakarCog
from ..channel_settings import ChannelSettings
from ..ui import LayoutView, format_list_or_inline

VIEW_AUDIT_LOG_PERMS = discord.Permissions(view_audit_log=True)
MANAGE_MESSAGES_PERMS = discord.Permissions(manage_messages=True)
MANAGE_CHANNELS_PERMS = discord.Permissions(manage_channels=True)

# Discord won't bulk-delete messages older than 14 days.
PURGE_MAX_AGE_SECONDS = 14 * 24 * 3600

_REGEX_LITERAL_RE = re.compile(r"^/(.*)/([a-zA-Z]*)$")
_JS_REGEX_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def _compile_regex_flags(flags: str) -> int:
    result = 0
    for ch in flags:
        result |= _JS_REGEX_FLAG_MAP.get(ch, 0)
    return result


# ---- lock / unlock ----

CHANNEL_LOCK_PERMISSIONS = (
    "send_messages",
    "add_reactions",
    "send_messages_in_threads",
    "create_public_threads",
    "create_private_threads",
)

LOCKABLE_CHANNEL_TYPES = (
    discord.ChannelType.text,
    discord.ChannelType.voice,
    discord.ChannelType.news,
    discord.ChannelType.news_thread,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.forum,
)

_LOCK_STRINGS = {
    "action": "locked",
    "modal_title": "Lock channels",
    "channels_description": "Select which channels you want to lock",
    "message_description": "Optional message to send in the locked channels",
    "no_channels": "There are no channels to lock.",
    "success": "Successfully locked channels: ",
}
_UNLOCK_STRINGS = {
    "action": "unlocked",
    "modal_title": "Unlock channels",
    "channels_description": "Select which channels you want to unlock",
    "message_description": "Optional message to send in the unlocked channels",
    "no_channels": "There are no locked channels.",
    "success": "Successfully unlocked channels: ",
}


def _is_lockable(channel: discord.abc.GuildChannel) -> bool:
    if channel.type not in LOCKABLE_CHANNEL_TYPES:
        return False
    everyone_perms = channel.permissions_for(channel.guild.default_role)
    if not everyone_perms.view_channel:
        return False
    return any(getattr(everyone_perms, perm) for perm in CHANNEL_LOCK_PERMISSIONS)


async def _lock_channel(
    channel_settings: ChannelSettings, channel: discord.abc.GuildChannel, everyone: discord.Role, message: Optional[str]
) -> bool:
    """Denies every currently-granted lock-permission for @everyone in this channel,
    saving what each one's prior state was (an explicit allow, vs. no overwrite at
    all) so /unlock can restore it exactly. Returns False (no-op) if @everyone has
    none of those permissions here to begin with."""
    everyone_perms = channel.permissions_for(everyone)
    overwrite = channel.overwrites_for(everyone)

    lock_state: dict[str, Optional[bool]] = {}
    for perm in CHANNEL_LOCK_PERMISSIONS:
        if not getattr(everyone_perms, perm):
            continue
        lock_state[perm] = True if getattr(overwrite, perm) is True else None
        setattr(overwrite, perm, False)

    if not lock_state:
        return False

    await channel.set_permissions(everyone, overwrite=overwrite, reason="Channel locked")
    await channel_settings.set_lock(channel.guild.id, channel.id, lock_state)

    text = "**This channel has been locked**"
    if message:
        text += f"\n{message}"
    text += "\n-# You are not muted, this channel is locked for everyone. Please don't DM people."
    try:
        await channel.send(text)
    except discord.HTTPException:
        pass
    return True


async def _unlock_channel(
    channel_settings: ChannelSettings, channel: discord.abc.GuildChannel, everyone: discord.Role, message: Optional[str]
) -> bool:
    """Restores whatever /lock saved for this channel. Returns False (no-op) if
    there's no saved lock state at all (never locked, or already unlocked)."""
    lock_state = await channel_settings.get_lock(channel.id)
    if not lock_state:
        return False

    overwrite = channel.overwrites_for(everyone)
    for perm, value in lock_state.items():
        setattr(overwrite, perm, value)
    await channel.set_permissions(everyone, overwrite=overwrite, reason="Channel unlocked")
    await channel_settings.set_lock(channel.guild.id, channel.id, {})

    text = "**This channel has been unlocked**"
    if message:
        text += f"\n{message}"
    try:
        await channel.send(text)
    except discord.HTTPException:
        pass
    return True


class ChannelActionModal(discord.ui.DesignerModal):
    def __init__(
        self,
        cog: "GuildCog",
        action: str,
        everyone: discord.Role,
        is_global: bool,
        global_channels: Optional[list[discord.abc.GuildChannel]],
    ):
        self.cog = cog
        self.action = action
        self.everyone = everyone
        self.is_global = is_global
        self.global_channels = global_channels
        strings = _LOCK_STRINGS if action == "lock" else _UNLOCK_STRINGS

        self.channel_select: Optional[discord.ui.Select] = None
        items = []
        if not is_global:
            self.channel_select = discord.ui.ChannelSelect(
                min_values=1, max_values=25, channel_types=list(LOCKABLE_CHANNEL_TYPES)
            )
            items.append(
                discord.ui.Label(
                    label="Channels", description=strings["channels_description"], item=self.channel_select
                )
            )

        self.message_input = discord.ui.InputText(
            style=discord.InputTextStyle.long, required=False, max_length=4000
        )
        items.append(
            discord.ui.Label(
                label=f"{action.title()} message", description=strings["message_description"], item=self.message_input
            )
        )

        super().__init__(*items, title=strings["modal_title"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        strings = _LOCK_STRINGS if self.action == "lock" else _UNLOCK_STRINGS
        channels = self.global_channels if self.is_global else list(self.channel_select.values)
        message = self.message_input.value or None
        perform = _lock_channel if self.action == "lock" else _unlock_channel

        acted = []
        for channel in channels:
            if await perform(self.cog.channel_settings, channel, self.everyone, message):
                acted.append(channel)

        if not acted:
            await interaction.followup.send(strings["no_channels"], ephemeral=True)
            return
        await interaction.followup.send(
            strings["success"] + ", ".join(c.mention for c in acted), ephemeral=True
        )


class GuildCog(DakarCog, name="Guild"):
    @discord.slash_command(
        name="server", description="Show information about this server.",
        default_member_permissions=VIEW_AUDIT_LOG_PERMS,
    )
    async def server(self, ctx: discord.ApplicationContext) -> None:
        guild = ctx.guild
        owner = guild.owner or await guild.fetch_member(guild.owner_id)

        lines = []
        if guild.description:
            lines.append(f"**Description:** {guild.description}")
        lines += [
            f"**Owner:** {owner.mention}",
            f"**Owner ID:** {owner.id}",
            f"**Created:** <t:{int(guild.created_at.timestamp())}:F>",
            f"**Guild ID:** {guild.id}",
        ]
        stats_lines = [
            f"**Members:** {guild.member_count}",
            f"**Member Limit:** {guild.max_members}",
            f"**Verified:** {'Yes' if 'VERIFIED' in guild.features else 'No'}",
            f"**Partnered:** {'Yes' if 'PARTNERED' in guild.features else 'No'}",
            f"**Premium Tier:** {guild.premium_tier}",
        ]
        features_text = "**Features:**\n" + format_list_or_inline(sorted(guild.features))

        heading = discord.ui.TextDisplay(f"## {guild.name}")
        if guild.icon:
            top = discord.ui.Section(heading, accessory=discord.ui.Thumbnail(url=guild.icon.url))
        else:
            top = heading
        container = discord.ui.Container(
            top,
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.Separator(),
            discord.ui.TextDisplay("\n".join(stats_lines)),
            discord.ui.Separator(),
            discord.ui.TextDisplay(features_text),
            color=discord.Color.red(),
        )
        await ctx.respond(view=LayoutView(container))

    @discord.slash_command(
        name="role", description="Show information about a role.", default_member_permissions=VIEW_AUDIT_LOG_PERMS
    )
    async def role(
        self, ctx: discord.ApplicationContext, role: discord.Option(discord.Role, description="The role to view")
    ) -> None:
        if role.permissions.administrator:
            permissions_text = "Administrator"
        else:
            granted = [name.replace("_", " ").title() for name, value in role.permissions if value]
            permissions_text = format_list_or_inline(granted)

        lines = [
            f"**Created:** <t:{int(role.created_at.timestamp())}:F>",
            f"**Managed:** {'Yes' if role.managed else 'No'}",
            f"**Hoisted:** {'Yes' if role.hoist else 'No'}",
            f"**Color:** {str(role.color)}",
        ]
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## {role.name}"),
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"**Permissions:**\n{permissions_text}"),
            color=role.color if role.color.value else discord.Color.default(),
        )
        await ctx.respond(view=LayoutView(container))

    @discord.slash_command(
        name="id", description="Search for users in the member and ban list.",
        default_member_permissions=VIEW_AUDIT_LOG_PERMS,
    )
    async def id_search(
        self,
        ctx: discord.ApplicationContext,
        query: discord.Option(str, description="Username to search for", min_length=2, max_length=37),
    ) -> None:
        await ctx.defer()

        members = await ctx.guild.query_members(query=query, limit=25)
        lower_query = query.lower()

        matched_bans = []
        async for ban_entry in ctx.guild.bans(limit=None):
            names = [n for n in (ban_entry.user.name, ban_entry.user.global_name) if n]
            if any(lower_query in name.lower() for name in names):
                matched_bans.append(ban_entry.user)
                if len(matched_bans) >= 25:
                    break

        if not members and not matched_bans:
            await ctx.respond("No users found.")
            return

        lines = [f"{discord.utils.escape_markdown(m.display_name)}: {m.id}" for m in members]
        lines += [f"{discord.utils.escape_markdown(u.display_name)}: {u.id}" for u in matched_bans]

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## {len(lines)} results for '{query}'"),
            discord.ui.TextDisplay("\n".join(lines)),
            color=discord.Color.green(),
        )
        await ctx.respond(view=LayoutView(container))

    @discord.slash_command(
        name="purge", description="Bulk delete messages matching a filter.",
        default_member_permissions=MANAGE_MESSAGES_PERMS,
    )
    async def purge(
        self,
        ctx: discord.ApplicationContext,
        content: discord.Option(str, description="Only delete messages including this string", required=False, default=None),
        author: discord.Option(discord.User, description="Only delete messages sent by this user", required=False, default=None),
        regex: discord.Option(str, description="Only delete messages matching this regex, e.g. '/foo/i'", required=False, default=None),
        limit: discord.Option(
            int, description="Test filters against the last x messages (default: 100)",
            required=False, default=100, min_value=1, max_value=1000,
        ),
    ) -> None:
        await ctx.defer(ephemeral=True)

        compiled_regex = None
        if regex:
            match = _REGEX_LITERAL_RE.match(regex)
            if not match:
                await ctx.respond(f"Invalid regex: `{regex}`", ephemeral=True)
                return
            try:
                compiled_regex = re.compile(match.group(1), _compile_regex_flags(match.group(2)))
            except re.error:
                await ctx.respond(f"Invalid regex: `{regex}`", ephemeral=True)
                return

        cutoff = discord.utils.utcnow() - datetime.timedelta(seconds=PURGE_MAX_AGE_SECONDS)
        lowered_content = content.lower() if content else None

        def matches(message: discord.Message) -> bool:
            if message.created_at < cutoff:
                return False
            if lowered_content and lowered_content not in message.content.lower():
                return False
            if author and message.author.id != author.id:
                return False
            if compiled_regex and not compiled_regex.search(message.content):
                return False
            return True

        to_delete = [msg async for msg in ctx.channel.history(limit=limit) if matches(msg)]
        if not to_delete:
            await ctx.respond("No messages matched your filters.", ephemeral=True)
            return

        await ctx.channel.delete_messages(to_delete)
        await self._log_purge(ctx, len(to_delete), limit, content, author, regex)
        await ctx.respond(f"Deleted {len(to_delete)} messages.", ephemeral=True)

    @discord.slash_command(
        name="lock", description="Stop users from sending messages and adding reactions in one or more channels.",
        default_member_permissions=MANAGE_CHANNELS_PERMS,
    )
    async def lock(
        self,
        ctx: discord.ApplicationContext,
        global_: discord.Option(bool, description="Lock all lockable channels", name="global", required=False, default=False),
    ) -> None:
        channels = None
        if global_:
            channels = [c for c in ctx.guild.channels if _is_lockable(c)]
            if not channels:
                await ctx.respond(_LOCK_STRINGS["no_channels"], ephemeral=True)
                return
        await ctx.send_modal(ChannelActionModal(self, "lock", ctx.guild.default_role, global_, channels))

    @discord.slash_command(
        name="unlock", description="Unlock locked channels.", default_member_permissions=MANAGE_CHANNELS_PERMS
    )
    async def unlock(
        self,
        ctx: discord.ApplicationContext,
        global_: discord.Option(bool, description="Unlock all locked channels", name="global", required=False, default=False),
    ) -> None:
        channels = None
        if global_:
            locked_ids = await self.channel_settings.get_locked_channel_ids(ctx.guild.id)
            channels = [c for c in (ctx.guild.get_channel(cid) for cid in locked_ids) if c is not None]
            if not channels:
                await ctx.respond(_UNLOCK_STRINGS["no_channels"], ephemeral=True)
                return
        await ctx.send_modal(ChannelActionModal(self, "unlock", ctx.guild.default_role, global_, channels))

    async def _log_purge(
        self,
        ctx: discord.ApplicationContext,
        count: int,
        limit: int,
        content: Optional[str],
        author: Optional[discord.User],
        regex: Optional[str],
    ) -> None:
        log_channel_id = await self.guild_settings.get_log_channel_id(ctx.guild.id)
        if not log_channel_id:
            return
        channel = ctx.guild.get_channel(log_channel_id)
        if channel is None:
            return

        lines = [
            f"**Moderator ID:** {ctx.author.id}",
            f"**Tested messages:** {limit}",
            "",
            "**Filters:**",
            f"**Channel:** {ctx.channel.mention}",
        ]
        if content:
            lines.append(f"**Content:** {content}")
        if author:
            lines.append(f"**User:** {author.mention}")
        if regex:
            lines.append(f"**Regex:** `{regex}`")

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"**{ctx.author.display_name} purged {count} messages**"),
            discord.ui.TextDisplay("\n".join(lines)),
            color=discord.Color.red(),
        )
        try:
            await channel.send(view=LayoutView(container))
        except discord.HTTPException:
            pass


def setup(bot: DakarBot) -> None:
    bot.add_cog(GuildCog(bot))
