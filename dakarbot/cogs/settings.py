"""Settings cog: the /settings command tree for configuring the bot per guild.

Ports modbot's src/commands/settings/SettingsCommand.js and its children. Scope
decisions made while porting:

- help-center and playlist are deferred entirely to the integrations cog (no Zendesk/
  YouTube integration exists yet to consume them, and both involve live external
  validation calls plus a per-guild command-resync mechanic that's a genuine
  discord.js/pycord architecture gap — not worth building against nothing).
- safe-search is skipped: it's gated behind a bot-operator-level feature whitelist
  that doesn't exist in this port, and nothing consumes it yet (Cloud Vision isn't
  built). /safe-search is also a top-level command in modbot, not part of this tree.
- The channel-scoped invites override doesn't get its own ChannelSettings table/class
  (modbot has one) — it's the only channel-scoped setting in the entire tree, so it's
  a nested map inside the guild's own settings blob instead (see guild_settings.py).
"""

import logging
from typing import Awaitable, Callable, Optional

import discord

from ..bot import DakarBot, DakarCog
from ..ui import Icon, LayoutView, format_list_or_inline
from ..moderation import models
from ..moderation.duration import format_duration, parse_duration
from ..moderation.models import ModerationAction, Punishment, PunishmentAction, resolve_action_emoji

logger = logging.getLogger("dakarbot.settings")

MANAGE_GUILD = discord.Permissions(manage_guild=True)

MUTE_ROLE_DENY_PERMS = (
    "send_messages",
    "add_reactions",
    "speak",
    "send_messages_in_threads",
    "create_public_threads",
)


def _role_is_editable(guild: discord.Guild, role: discord.Role) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False
    return (
        bot_member.guild_permissions.manage_roles
        and bot_member.top_role > role
        and not role.managed
        and role != guild.default_role
    )


def _channel_usable(guild: discord.Guild, channel: discord.TextChannel) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False
    perms = channel.permissions_for(bot_member)
    return perms.view_channel and perms.send_messages


def _channel_or_disabled(channel_id: Optional[int]) -> str:
    return f"<#{channel_id}>" if channel_id else "Disabled"


def _cooldown_or_disabled(seconds: int) -> str:
    return format_duration(seconds) if seconds >= 0 else "Disabled"


def _threshold_or_disabled(value: int) -> str:
    return str(value) if value >= 0 else "Disabled"


def _describe_punishment(strikes: int, punishment: Punishment) -> str:
    # PunishmentAction.NONE has no ModerationAction counterpart.
    is_none = punishment.action is PunishmentAction.NONE
    emoji = "" if is_none else resolve_action_emoji(ModerationAction(punishment.action.value))
    text = f"{emoji} At **{strikes}** strikes: **{punishment.action.value}**"
    if punishment.duration:
        text += f" for {format_duration(punishment.duration)}"
    return text


PUNISHMENT_CHOICES = [
    discord.OptionChoice(name="Ban", value="ban"),
    discord.OptionChoice(name="Kick", value="kick"),
    discord.OptionChoice(name="Mute", value="mute"),
    discord.OptionChoice(name="Softban", value="softban"),
    discord.OptionChoice(name="Remove punishment", value="none"),
]

INVITES_CHOICES = [
    discord.OptionChoice(name="Allowed", value="allowed"),
    discord.OptionChoice(name="Forbidden", value="forbidden"),
    discord.OptionChoice(name="Default (inherit server setting)", value="default"),
]


class SettingsCog(DakarCog, name="Settings"):
    async def _set_log_channel(
        self,
        ctx: discord.ApplicationContext,
        channel: Optional[discord.TextChannel],
        label: str,
        setter: Callable[[int, Optional[int]], Awaitable[None]],
    ) -> None:
        """Shared body for log-channel, message-log, and join-log: validate the bot
        can actually post there, save it (or clear it), and confirm."""
        if channel is not None and not _channel_usable(ctx.guild, channel):
            await ctx.respond(
                f"{Icon.ERROR} I don't have permission to send messages in {channel.mention}.", ephemeral=True
            )
            return
        await setter(ctx.guild.id, channel.id if channel else None)
        icon = Icon.SUCCESS if channel else "🚫"
        await ctx.respond(f"{icon} {label} set to {channel.mention}." if channel else f"{icon} {label} disabled.")

    # ---- top-level group ----

    settings = discord.SlashCommandGroup(
        "settings", "Configure the bot for this server.", default_member_permissions=MANAGE_GUILD
    )

    @settings.command(name="overview", description="Show the current settings for this server.")
    async def overview(self, ctx: discord.ApplicationContext) -> None:
        gs = self.guild_settings
        guild_id = ctx.guild.id

        muted_role_id = await gs.get_muted_role_id(guild_id)
        moderation_text = (
            f"### {Icon.SHIELD} Moderation\n"
            f"📝 Log channel: {_channel_or_disabled(await gs.get_log_channel_id(guild_id))}\n"
            f"✏️ Message log: {_channel_or_disabled(await gs.get_message_log_channel_id(guild_id))}\n"
            f"👋 Join log: {_channel_or_disabled(await gs.get_join_log_channel_id(guild_id))}\n"
            f"🔇 Muted role: {f'<@&{muted_role_id}>' if muted_role_id else 'Disabled'}"
        )

        protected_role_ids = await gs.get_protected_role_ids(guild_id)
        protected_text = "### 🚫 Protected Roles\n" + format_list_or_inline(
            [f"<@&{r}>" for r in protected_role_ids]
        )

        automod_text = (
            f"### {Icon.AUTOMOD} Automod\n"
            f"🔗 Invites: {'Allowed' if await gs.get_invites_allowed(guild_id) else 'Forbidden'}\n"
            f"⏱️ Link cooldown: {_cooldown_or_disabled(await gs.get_link_cooldown(guild_id))}\n"
            f"📎 Attachment cooldown: {_cooldown_or_disabled(await gs.get_attachment_cooldown(guild_id))}\n"
            f"🔠 Caps filter: {'Enabled' if await gs.get_caps_enabled(guild_id) else 'Disabled'}\n"
            f"💬 Spam threshold: {_threshold_or_disabled(await gs.get_anti_spam(guild_id))}\n"
            f"🔁 Similar messages threshold: {_threshold_or_disabled(await gs.get_similar_messages(guild_id))}"
        )

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## {Icon.SERVER} {ctx.guild.name} Settings"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(moderation_text),
            discord.ui.Separator(),
            discord.ui.TextDisplay(protected_text),
            discord.ui.Separator(),
            discord.ui.TextDisplay(automod_text),
            color=discord.Color.blue(),
        )
        await ctx.respond(view=LayoutView(container))

    @settings.command(name="log-channel", description="Set (or disable) the moderation log channel.")
    async def log_channel(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel, description="Leave empty to disable", required=False, default=None
        ),
    ) -> None:
        await self._set_log_channel(ctx, channel, "Log channel", self.guild_settings.set_log_channel_id)

    @settings.command(name="message-log", description="Set (or disable) the message edit/delete log channel.")
    async def message_log(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel, description="Leave empty to disable", required=False, default=None
        ),
    ) -> None:
        await self._set_log_channel(
            ctx, channel, "Message log channel", self.guild_settings.set_message_log_channel_id
        )

    @settings.command(name="join-log", description="Set (or disable) the member join/leave log channel.")
    async def join_log(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel, description="Leave empty to disable", required=False, default=None
        ),
    ) -> None:
        await self._set_log_channel(
            ctx, channel, "Join log channel", self.guild_settings.set_join_log_channel_id
        )

    @settings.command(name="caps", description="Enable or disable the excessive-caps filter.")
    async def caps(
        self,
        ctx: discord.ApplicationContext,
        enabled: discord.Option(bool, description="Whether to filter excessive caps"),
    ) -> None:
        await self.guild_settings.set_caps_enabled(ctx.guild.id, enabled)
        icon = Icon.SUCCESS if enabled else "🚫"
        await ctx.respond(f"{icon} Caps filter {'enabled' if enabled else 'disabled'}.")

    @settings.command(name="spam", description="Set the message-per-minute spam threshold.")
    async def spam(
        self,
        ctx: discord.ApplicationContext,
        value: discord.Option(
            int, description="Messages/minute before action; omit to disable", min_value=1, max_value=60,
            required=False, default=None,
        ),
    ) -> None:
        resolved = value if value is not None else -1
        await self.guild_settings.set_anti_spam(ctx.guild.id, resolved)
        await ctx.respond(
            f"{Icon.SUCCESS} Spam threshold set to {value} messages/minute." if value else "🚫 Spam detection disabled."
        )

    @settings.command(name="similar-messages", description="Set the repeated/similar-message threshold.")
    async def similar_messages(
        self,
        ctx: discord.ApplicationContext,
        value: discord.Option(
            int, description="Similar messages before action; omit to disable", min_value=1, max_value=60,
            required=False, default=None,
        ),
    ) -> None:
        resolved = value if value is not None else -1
        await self.guild_settings.set_similar_messages(ctx.guild.id, resolved)
        await ctx.respond(
            f"{Icon.SUCCESS} Similar-messages threshold set to {value}."
            if value
            else "🚫 Similar-message detection disabled."
        )

    @settings.command(name="link-cool-down", description="Set the per-user cooldown between posting links.")
    async def link_cool_down(
        self,
        ctx: discord.ApplicationContext,
        cool_down: discord.Option(
            str, description="e.g. '10s', '1m'; omit to disable", required=False, default=None
        ),
    ) -> None:
        # An unparseable value disables the cooldown rather than erroring — matches
        # modbot's own (lenient) behavior here.
        seconds = parse_duration(cool_down) if cool_down else None
        resolved = seconds if seconds is not None else -1
        await self.guild_settings.set_link_cooldown(ctx.guild.id, resolved)
        await ctx.respond(
            f"{Icon.SUCCESS} Link cooldown set to {format_duration(resolved)}."
            if resolved >= 0
            else "🚫 Link cooldown disabled."
        )

    @settings.command(name="attachment-cool-down", description="Set the per-user cooldown between posting attachments.")
    async def attachment_cool_down(
        self,
        ctx: discord.ApplicationContext,
        cool_down: discord.Option(
            str, description="e.g. '10s', '1m'; omit to disable", required=False, default=None
        ),
    ) -> None:
        seconds = parse_duration(cool_down) if cool_down else None
        resolved = seconds if seconds is not None else -1
        await self.guild_settings.set_attachment_cooldown(ctx.guild.id, resolved)
        await ctx.respond(
            f"{Icon.SUCCESS} Attachment cooldown set to {format_duration(resolved)}."
            if resolved >= 0
            else "🚫 Attachment cooldown disabled."
        )

    # ---- /settings punishments ----

    punishments = settings.create_subgroup("punishments", "Configure the strike punishment ladder.")

    @punishments.command(name="show", description="Show the punishment ladder.")
    async def punishments_show(self, ctx: discord.ApplicationContext) -> None:
        ladder = await self.guild_settings.get_punishment_ladder(ctx.guild.id)
        if not ladder:
            await ctx.respond("No punishments configured.")
            return
        lines = [_describe_punishment(strikes, p) for strikes, p in sorted(ladder.items())]
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## {Icon.LADDER} Punishment Ladder"),
            discord.ui.TextDisplay("\n".join(f"- {line}" for line in lines)),
            color=discord.Color.blue(),
        )
        await ctx.respond(view=LayoutView(container))

    @punishments.command(name="set", description="Set (or remove) the punishment for a strike count.")
    async def punishments_set(
        self,
        ctx: discord.ApplicationContext,
        strike_count: discord.Option(int, description="Strike count threshold", min_value=0),
        punishment: discord.Option(str, description="Punishment to apply", choices=PUNISHMENT_CHOICES),
        duration: discord.Option(
            str, description="e.g. '1d' (ban/mute only)", required=False, default=None
        ),
    ) -> None:
        if punishment == "none":
            removed = await self.guild_settings.remove_punishment(ctx.guild.id, strike_count)
            await ctx.respond(
                f"{Icon.SUCCESS} Removed the punishment at {strike_count} strikes."
                if removed
                else f"There was no punishment set at {strike_count} strikes."
            )
            return

        try:
            duration_seconds = parse_duration(duration) if duration else None
        except ValueError as exc:
            # Falling back to no duration here would turn a too-long ban into a permanent one.
            await ctx.respond(str(exc), ephemeral=True)
            return
        entry = Punishment(action=PunishmentAction(punishment), duration=duration_seconds, count=strike_count)
        await self.guild_settings.set_punishment(ctx.guild.id, strike_count, entry)
        await ctx.respond(_describe_punishment(strike_count, entry))

    # ---- /settings protected-roles ----

    protected_roles = settings.create_subgroup("protected-roles", "Manage roles immune to moderation.")

    @protected_roles.command(name="add", description="Protect a role from moderation.")
    async def protected_roles_add(
        self, ctx: discord.ApplicationContext, role: discord.Option(discord.Role, description="Role to protect")
    ) -> None:
        added = await self.guild_settings.add_protected_role(ctx.guild.id, role.id)
        if added:
            await ctx.respond(f"{Icon.SHIELD} {role.mention} is now protected.")
        else:
            await ctx.respond(f"{role.mention} is already protected.", ephemeral=True)

    @protected_roles.command(name="remove", description="Remove a role's protection from moderation.")
    async def protected_roles_remove(
        self, ctx: discord.ApplicationContext, role: discord.Option(discord.Role, description="Role to unprotect")
    ) -> None:
        removed = await self.guild_settings.remove_protected_role(ctx.guild.id, role.id)
        if removed:
            await ctx.respond(f"🚫 {role.mention} is no longer protected.")
        else:
            await ctx.respond(f"{role.mention} isn't protected.", ephemeral=True)

    @protected_roles.command(name="list", description="List protected roles.")
    async def protected_roles_list(self, ctx: discord.ApplicationContext) -> None:
        role_ids = await self.guild_settings.get_protected_role_ids(ctx.guild.id)
        valid_ids: set[int] = set()
        mentions = []
        for role_id in role_ids:
            role = ctx.guild.get_role(role_id)
            if role is not None:
                valid_ids.add(role_id)
                mentions.append(role.mention)
        if valid_ids != role_ids:
            # Self-heal: drop role ids that no longer resolve in the guild.
            await self.guild_settings.set_protected_role_ids(ctx.guild.id, valid_ids)
        if not mentions:
            await ctx.respond("No protected roles configured.")
            return
        await ctx.respond(f"{Icon.SHIELD} **Protected roles:**\n" + format_list_or_inline(mentions))

    # ---- /settings muted-role ----

    muted_role = settings.create_subgroup("muted-role", "Configure the role used for long/permanent mutes.")

    async def _apply_muted_role(self, ctx: discord.ApplicationContext, role: discord.Role) -> bool:
        """Runs the hierarchy check, channel-overwrite setup, and old-role member
        migration for a new muted role. Returns False (and replies with an error)
        without changing anything if it can't proceed."""
        if not _role_is_editable(ctx.guild, role):
            await ctx.respond(
                "I can't manage that role — check my role position and permissions.", ephemeral=True
            )
            return False

        old_role_id = await self.guild_settings.get_muted_role_id(ctx.guild.id)
        old_role = ctx.guild.get_role(old_role_id) if old_role_id else None
        if old_role is not None and old_role.id != role.id and not _role_is_editable(ctx.guild, old_role):
            await ctx.respond(
                f"I can't manage the current muted role ({old_role.mention}) to migrate members off of it.",
                ephemeral=True,
            )
            return False

        await ctx.defer()

        for channel in ctx.guild.channels:
            perms = channel.permissions_for(ctx.guild.me)
            if not perms.manage_roles:
                continue
            overwrite = channel.overwrites_for(role)
            if any(getattr(overwrite, perm) is not False for perm in MUTE_ROLE_DENY_PERMS):
                for perm in MUTE_ROLE_DENY_PERMS:
                    setattr(overwrite, perm, False)
                try:
                    await channel.set_permissions(role, overwrite=overwrite, reason="Muted role setup")
                except discord.HTTPException:
                    logger.exception("Failed to set muted-role overwrite in channel %s", channel.id)

        if old_role is not None and old_role.id != role.id:
            active_mutes = await models.get_all_active(self.db, ctx.guild.id, ModerationAction.MUTE)
            for record in active_mutes:
                member = ctx.guild.get_member(record.user_id)
                if member is None:
                    continue
                try:
                    if old_role in member.roles:
                        await member.remove_roles(old_role, reason="Muted role changed")
                    await member.add_roles(role, reason="Muted role changed")
                except discord.HTTPException:
                    logger.exception("Failed to migrate member %s to new muted role", member.id)

        await self.guild_settings.set_muted_role_id(ctx.guild.id, role.id)
        return True

    @muted_role.command(name="create", description="Create and set a new muted role.")
    async def muted_role_create(self, ctx: discord.ApplicationContext) -> None:
        role = await ctx.guild.create_role(name="Muted", hoist=False, reason="Muted role setup")
        if await self._apply_muted_role(ctx, role):
            await ctx.respond(f"🔇 Created and set the muted role to {role.mention}.")

    @muted_role.command(name="set", description="Use an existing role as the muted role.")
    async def muted_role_set(
        self, ctx: discord.ApplicationContext, role: discord.Option(discord.Role, description="Role to use")
    ) -> None:
        if await self._apply_muted_role(ctx, role):
            await ctx.respond(f"🔇 Muted role set to {role.mention}.")

    @muted_role.command(name="disable", description="Disable the muted role.")
    async def muted_role_disable(self, ctx: discord.ApplicationContext) -> None:
        await self.guild_settings.set_muted_role_id(ctx.guild.id, None)
        await ctx.respond("🚫 Muted role disabled. This does not unmute anyone currently muted with it.")

    # ---- /settings invites ----

    invites = settings.create_subgroup("invites", "Configure whether invite links are allowed.")

    @invites.command(name="show", description="Show the current invite-link policy.")
    async def invites_show(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel, description="Check a specific channel's override", required=False, default=None
        ),
    ) -> None:
        guild_default = await self.guild_settings.get_invites_allowed(ctx.guild.id)
        if channel is not None:
            override = await self.guild_settings.get_channel_invites_override(ctx.guild.id, channel.id)
            effective = override if override is not None else guild_default
            source = "channel override" if override is not None else "server default"
            await ctx.respond(
                f"🔗 Invites are **{'allowed' if effective else 'forbidden'}** in {channel.mention} ({source})."
            )
        else:
            await ctx.respond(f"🔗 Invites are **{'allowed' if guild_default else 'forbidden'}** by default.")

    @invites.command(name="set", description="Set the invite-link policy, server-wide or for one channel.")
    async def invites_set(
        self,
        ctx: discord.ApplicationContext,
        invites: discord.Option(str, description="Policy", choices=INVITES_CHOICES),
        channel: discord.Option(
            discord.TextChannel, description="Leave empty to set the server default", required=False, default=None
        ),
    ) -> None:
        if invites == "default" and channel is None:
            await ctx.respond("`default` is only valid for a channel override.", ephemeral=True)
            return

        if channel is not None:
            value = None if invites == "default" else (invites == "allowed")
            await self.guild_settings.set_channel_invites_override(ctx.guild.id, channel.id, value)
            if value is None:
                await ctx.respond(f"🔗 {channel.mention} now inherits the server default for invites.")
            else:
                await ctx.respond(f"🔗 Invites are now **{'allowed' if value else 'forbidden'}** in {channel.mention}.")
        else:
            allowed = invites == "allowed"
            await self.guild_settings.set_invites_allowed(ctx.guild.id, allowed)
            await ctx.respond(f"🔗 Invites are now **{'allowed' if allowed else 'forbidden'}** by default.")


def setup(bot: DakarBot) -> None:
    bot.add_cog(SettingsCog(bot))
