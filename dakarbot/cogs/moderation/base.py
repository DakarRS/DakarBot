"""ModerationCog: the actual @discord.slash_command / @user_command / @moderation.
command declarations pycord requires on the cog class itself, each one a thin
one-line delegate to its implementation in commands/<name>.py. Shared cross-command
state — the moderateable checks, the duplicate-moderation confirmation flow, case
reporting, the mute precheck — lives here since every command needs it and Python
can't split one class's methods across files the way per-command modules can be
split for everything else.

Deliberately NOT using `from __future__ import annotations`: pycord reads
discord.Option(...) directly out of each parameter's raw annotation at command-
registration time. With postponed evaluation active, every annotation becomes a
plain string instead of an actual Option instance, so pycord silently falls back to
treating the parameter as a plain string option — no error at load time, but every
option ends up wrong, and some crash at invoke time.

Ports modbot's src/commands/user/{Ban,Unban,SoftBan,Kick,Mute,Unmute,Strike,Pardon,
StrikePurge}Command.js and src/commands/moderation/*.js, the guildBanRemove listener
from BanRemoveEventListener.js (as on_member_unban below), and the guildMemberAdd
listener from RestoreMutedRoleEventListener.js (as on_member_join below).
"""

import logging
import time
from typing import Awaitable, Callable, Optional

import discord
from discord.ext import commands

from dakarbot.bot import DakarBot, DakarCog
from dakarbot.moderation import confirmation, models
from dakarbot.moderation.duration import format_duration
from dakarbot.moderation.models import (
    TIMEOUT_LIMIT,
    ModeratedMember,
    Moderation,
    ModerationAction,
    ModerationTarget,
    build_case_view,
    is_moderateable,
    is_moderateable_by,
)
from dakarbot.ui import LayoutView

from . import commands as cmd

logger = logging.getLogger("dakarbot.moderation")

MODERATE_PERMS = discord.Permissions(moderate_members=True)
BAN_PERMS = discord.Permissions(ban_members=True)
KICK_PERMS = discord.Permissions(kick_members=True)
STRIKE_PURGE_PERMS = discord.Permissions(moderate_members=True, manage_messages=True)


async def _id_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    bot: DakarBot = ctx.interaction.client  # type: ignore[assignment]
    guild_id = ctx.interaction.guild_id
    if guild_id is None or bot.db is None:
        return []
    value = ctx.value
    if value and value.isdigit():
        matches = await models.search_by_id(bot.db, guild_id, value)
    else:
        matches = await models.search_by_moderator(bot.db, guild_id, ctx.interaction.user.id)
    return [discord.OptionChoice(name=f"#{m.id} - {m.action.value}", value=str(m.id)) for m in matches]


class ModerationCog(DakarCog, name="Moderation"):
    # ---- shared flow (used by every command file via the `cog` argument) ----

    def _moderated_member(self, ctx: discord.ApplicationContext, target: ModerationTarget) -> ModeratedMember:
        return ModeratedMember(self.db, self.guild_settings, ctx.guild, target)

    async def _checks_pass(self, ctx: discord.ApplicationContext, target: ModerationTarget) -> bool:
        if not await is_moderateable(ctx.guild, self.guild_settings, target):
            await ctx.respond("I can't moderate this member!", ephemeral=True)
            return False
        if not is_moderateable_by(ctx.author, target):
            await ctx.respond("You can't moderate this member!", ephemeral=True)
            return False
        return True

    async def _run_guarded(
        self,
        ctx: discord.ApplicationContext,
        target: ModerationTarget,
        action_label: str,
        perform: Callable[[], Awaitable[Optional[Moderation]]],
        *,
        danger: bool = False,
    ) -> None:
        if not await self._checks_pass(ctx, target):
            return

        duplicates = await confirmation.find_duplicate_moderations(
            self.db, ctx.guild.id, target.id, ctx.author.id
        )
        if duplicates:
            container = confirmation.build_duplicate_warning_container(target, duplicates)
            record = await models.create_confirmation(
                self.db, {"guild_id": ctx.guild.id, "user_id": target.id}
            )

            async def run_and_report() -> None:
                moderation = await perform()
                await self._report_result(ctx, action_label, target, moderation)

            view = confirmation.ConfirmationView(self.db, record, run_and_report, container, danger=danger)
            await ctx.respond(view=view, ephemeral=True)
            view.message = await ctx.interaction.original_response()
            return

        await ctx.defer(ephemeral=True)
        moderation = await perform()
        await self._report_result(ctx, action_label, target, moderation)

    async def _report_result(
        self,
        ctx: discord.ApplicationContext,
        action_label: str,
        target: ModerationTarget,
        moderation: Optional[Moderation],
    ) -> None:
        if moderation is None:
            await ctx.respond(
                f"Couldn't {action_label} {target.mention} — check the bot's logs for details.",
                ephemeral=True,
            )
            return
        await ctx.respond(view=build_case_view(moderation), ephemeral=True)

    async def _mute_precheck(
        self, ctx: discord.ApplicationContext, duration_seconds: Optional[int]
    ) -> Optional[str]:
        """Returns an error message if a permanent/>28-day mute can't proceed because
        no usable muted role is configured, else None."""
        if duration_seconds and duration_seconds <= TIMEOUT_LIMIT:
            return None
        muted_role_id = await self.guild_settings.get_muted_role_id(ctx.guild.id)
        muted_role = ctx.guild.get_role(muted_role_id) if muted_role_id else None
        if muted_role is None:
            return "No valid muted role is configured for durations over 28 days (or permanent mutes)."
        bot_member = ctx.guild.me
        if bot_member is None or bot_member.top_role <= muted_role:
            return "My highest role isn't above the muted role, so I can't assign it."
        return None

    # ---- slash commands (thin: signature + one delegate call each) ----

    @discord.slash_command(name="ban", description="Ban a member.", default_member_permissions=BAN_PERMS)
    async def ban(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to ban"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
        duration: discord.Option(
            str, description="e.g. '1d', '2 weeks'; empty = permanent", required=False, default=None
        ),
        delete: discord.Option(
            str, description="How much message history to delete, e.g. '1h'", required=False, default=None
        ),
    ) -> None:
        await cmd.ban.run(self, ctx, user, reason, comment, duration, delete)

    @discord.slash_command(name="unban", description="Unban a user.", default_member_permissions=BAN_PERMS)
    async def unban(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.User, description="Who to unban"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
    ) -> None:
        await cmd.unban.run(self, ctx, user, reason, comment)

    @discord.slash_command(
        name="soft-ban", description="Ban then immediately unban, to purge message history.",
        default_member_permissions=BAN_PERMS,
    )
    async def soft_ban(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to soft-ban"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
        delete: discord.Option(
            str, description="How much message history to delete, e.g. '1h'", required=False, default=None
        ),
    ) -> None:
        await cmd.soft_ban.run(self, ctx, user, reason, comment, delete)

    @discord.slash_command(name="kick", description="Kick a member.", default_member_permissions=KICK_PERMS)
    async def kick(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to kick"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
    ) -> None:
        await cmd.kick.run(self, ctx, user, reason, comment)

    @discord.slash_command(
        name="mute", description="Timeout or mute a member.", default_member_permissions=MODERATE_PERMS
    )
    async def mute(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to mute"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
        duration: discord.Option(
            str, description="e.g. '30m', '1d'; empty = permanent", required=False, default=None
        ),
    ) -> None:
        await cmd.mute.run(self, ctx, user, reason, comment, duration)

    @discord.slash_command(
        name="unmute", description="Remove a member's mute.", default_member_permissions=MODERATE_PERMS
    )
    async def unmute(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to unmute"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
    ) -> None:
        await cmd.unmute.run(self, ctx, user, reason, comment)

    @discord.slash_command(
        name="strike", description="Add strikes to a member.", default_member_permissions=MODERATE_PERMS
    )
    async def strike(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to strike"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
        count: discord.Option(int, description="Strike count", min_value=1, max_value=100, default=1),
    ) -> None:
        await cmd.strike.run(self, ctx, user, reason, comment, count)

    @discord.slash_command(
        name="pardon", description="Pardon strikes from a member.", default_member_permissions=MODERATE_PERMS
    )
    async def pardon(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to pardon"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
        count: discord.Option(int, description="Strikes to pardon", min_value=1, max_value=100, default=1),
    ) -> None:
        await cmd.pardon.run(self, ctx, user, reason, comment, count)

    @discord.slash_command(
        name="strike-purge",
        description="Strike a member and delete their recent messages in this channel.",
        default_member_permissions=STRIKE_PURGE_PERMS,
    )
    async def strike_purge(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, description="Who to strike"),
        reason: discord.Option(str, description="Reason", required=False, default="No reason provided."),
        comment: discord.Option(str, description="Internal note", required=False, default=None),
        count: discord.Option(int, description="Strike count", min_value=1, max_value=100, default=1),
        limit: discord.Option(
            int, description="How many recent channel messages to scan", min_value=1, max_value=1000, default=100
        ),
    ) -> None:
        await cmd.strike_purge.run(self, ctx, user, reason, comment, count, limit)

    # ---- context menu commands ----

    @discord.user_command(name="Ban", default_member_permissions=BAN_PERMS)
    async def ban_context_menu(self, ctx: discord.ApplicationContext, member: discord.Member) -> None:
        await ctx.send_modal(cmd.ban.BanModal(self, member))

    @discord.user_command(name="Kick", default_member_permissions=KICK_PERMS)
    async def kick_context_menu(self, ctx: discord.ApplicationContext, member: discord.Member) -> None:
        await ctx.send_modal(cmd.kick.KickModal(self, member))

    @discord.user_command(name="Mute", default_member_permissions=MODERATE_PERMS)
    async def mute_context_menu(self, ctx: discord.ApplicationContext, member: discord.Member) -> None:
        await ctx.send_modal(cmd.mute.MuteModal(self, member))

    @discord.user_command(name="Strike", default_member_permissions=MODERATE_PERMS)
    async def strike_context_menu(self, ctx: discord.ApplicationContext, member: discord.Member) -> None:
        await ctx.send_modal(cmd.strike.StrikeModal(self, member))

    # ---- /moderation command group ----

    moderation = discord.SlashCommandGroup(
        "moderation",
        "View and manage moderation case records.",
        default_member_permissions=MODERATE_PERMS,
    )

    @moderation.command(name="show", description="Show a moderation case.")
    async def show(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Case ID", autocomplete=_id_autocomplete),
    ) -> None:
        await cmd.show.run(self, ctx, id)

    @moderation.command(name="edit", description="Edit a moderation case.")
    async def edit(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Case ID", autocomplete=_id_autocomplete),
        reason: discord.Option(str, description="New reason", required=False, default=None),
        comment: discord.Option(str, description="New internal note", required=False, default=None),
        duration: discord.Option(
            str, description="New duration (only for active cases)", required=False, default=None
        ),
        count: discord.Option(
            int, description="New strike/pardon count", required=False, default=None
        ),
    ) -> None:
        await cmd.edit.run(self, ctx, id, reason, comment, duration, count)

    @moderation.command(name="delete", description="Delete a moderation case.")
    async def delete(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Case ID", autocomplete=_id_autocomplete),
    ) -> None:
        await cmd.delete.run(self, ctx, id)

    @moderation.command(name="list", description="List a member's moderation history.")
    async def list_cases(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.User, description="Whose moderations to list"),
    ) -> None:
        await cmd.list_cases.run(self, ctx, user)

    @moderation.command(name="clear", description="Permanently delete all moderations for a member.")
    async def clear(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.User, description="Whose moderations to clear"),
    ) -> None:
        await cmd.clear.run(self, ctx, user)

    # ---- events ----

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        """Detects a ban being removed outside the bot (Discord UI, another bot, or an
        expired temp ban's own unban call already handles that case separately) and
        keeps the DB's active-ban state in sync. Matches BanRemoveEventListener.js —
        this only deactivates the row, it never creates an `unban` moderation record
        or re-runs MemberWrapper.unban(), to avoid double-logging. See the logging
        cog's audit-log handler for the attributed case log this defers to.
        """
        active_ban = await models.get_active(self.db, guild.id, user.id, ModerationAction.BAN)
        if active_ban is None:
            return
        await models.disable_active(self.db, guild.id, user.id, ModerationAction.BAN)
        remaining = ""
        if active_ban.expire_time:
            remaining = f" (had {format_duration(active_ban.expire_time - int(time.time()))} remaining)"
        logger.info("Guild %s: ban on user %s was removed outside the bot%s", guild.id, user.id, remaining)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Detects a member with an active mute rejoining and reapplies the muted
        role — matches RestoreMutedRoleEventListener.js. Note this only ever
        restores the *role*, never a native Discord timeout, matching modbot
        exactly: a native timeout doesn't survive a leave/rejoin either way, and
        modbot's own listener never re-applies one on join — only mutes that used
        the muted role (i.e. permanent or >28-day mutes; see TIMEOUT_LIMIT) are
        rejoin-evasion-proof here, same as in modbot.
        """
        if member.communication_disabled_until is not None:
            return  # already has an active native timeout somehow — nothing to restore

        mute = await models.get_active(self.db, member.guild.id, member.id, ModerationAction.MUTE)
        if mute is None:
            return

        muted_role_id = await self.guild_settings.get_muted_role_id(member.guild.id)
        muted_role = member.guild.get_role(muted_role_id) if muted_role_id else None
        if muted_role is None:
            return

        try:
            await member.add_roles(muted_role, reason="Restoring mute from before member left")
        except discord.NotFound:
            return  # member left again / role deleted mid-flight
        except discord.Forbidden:
            logger.warning(
                "Missing permissions to restore muted role for member %s in guild %s",
                member.id,
                member.guild.id,
            )
            return

        log_channel_id = await self.guild_settings.get_log_channel_id(member.guild.id)
        if not log_channel_id:
            return
        channel = member.guild.get_channel(log_channel_id)
        if channel is None:
            return
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## Restored mute — {member.display_name}"),
            discord.ui.TextDisplay(f"Mute ID: {mute.id}"),
            discord.ui.TextDisplay(f"-# User ID: {member.id}"),
            color=discord.Color.orange(),
        )
        try:
            await channel.send(view=LayoutView(container))
        except discord.HTTPException:
            logger.warning("Failed to post mute-restore notice to log channel in guild %s", member.guild.id)
