"""Avatar + user-info utility cog. Ports modbot's src/commands/user/{Avatar,
UserInfo}Command.js.

The /user info panel's action row wires every button straight into the SAME modal
classes the moderation cog's own commands/context-menus use (BanModal, KickModal,
MuteModal, StrikeModal, and the Unban/Unmute/Pardon modals added alongside them in
this same pass) — matching modbot exactly: UserInfoCommand's buttons open the exact
same modals as BanCommand.executeButton etc. Since Discord's default_member_permissions
only gates *slash-command* invocations (not button clicks on a view this bot sends
manually), every action button re-checks the equivalent permission itself before
opening its modal — mirroring the gate each of those commands would enforce if
invoked directly.

Deliberately NOT using `from __future__ import annotations`: see moderation/base.py's
module docstring for why (pycord reads discord.Option(...) off raw annotations).
"""
from typing import Optional, Union

import discord

from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..moderation import models
from ..moderation.models import ModerationAction
from ..ui import LayoutView
from .moderation.commands import ban, kick, mute, pardon, strike, unban, unmute
from .moderation.commands.list_cases import ModerationListView

# Discord's largest selectable avatar size.
AVATAR_SIZE = 4096

VIEW_AUDIT_LOG_PERMS = discord.Permissions(view_audit_log=True)


def _avatar_url(user: discord.abc.User, member: Optional[discord.Member], use_server_profile: bool) -> str:
    if use_server_profile and member is not None:
        return member.display_avatar.with_size(AVATAR_SIZE).url
    return user.display_avatar.with_size(AVATAR_SIZE).url


def build_avatar_view(user: discord.abc.User, guild: Optional[discord.Guild], use_server_profile: bool) -> LayoutView:
    member = guild.get_member(user.id) if guild else None
    url = _avatar_url(user, member, use_server_profile)
    container = discord.ui.Container(
        discord.ui.TextDisplay(f"## Avatar of {discord.utils.escape_markdown(user.display_name)}"),
        discord.ui.MediaGallery(discord.MediaGalleryItem(url)),
        color=discord.Color.blue(),
    )
    return LayoutView(container)


async def _get_mute_info(
    db, guild: discord.Guild, target: Union[discord.Member, discord.User]
) -> Optional[tuple[str, Optional[str], Optional[int]]]:
    """Returns (reason, comment, expire_unix_or_None) if muted, else None — checks
    the DB's active mute row first, then falls back to a live native timeout,
    matching MemberWrapper.getMuteInfo()."""
    active = await models.get_active(db, guild.id, target.id, ModerationAction.MUTE)
    if active is not None:
        return active.reason, active.comment, active.expire_time
    if isinstance(target, discord.Member) and target.timed_out:
        end = int(target.communication_disabled_until.timestamp()) if target.communication_disabled_until else None
        return "Unknown (time-out)", None, end
    return None


async def _get_ban_info(
    db, guild: discord.Guild, user: discord.abc.User
) -> Optional[tuple[str, Optional[str], Optional[int]]]:
    """Returns (reason, comment, expire_unix_or_None) if banned, else None — checks
    the DB's active ban row first, then falls back to a live Discord ban entry,
    matching MemberWrapper.getBanInfo()."""
    active = await models.get_active(db, guild.id, user.id, ModerationAction.BAN)
    if active is not None:
        return active.reason, active.comment, active.expire_time
    try:
        ban_entry = await guild.fetch_ban(user)
    except discord.NotFound:
        return None
    return ban_entry.reason or "Unknown", None, None


class UserInfoView(LayoutView):
    """BaseView doesn't collect @discord.ui.button-decorated methods (that collection
    machinery only exists on View), so buttons are built and wired by hand here."""

    def __init__(
        self,
        cog: "UserInfoCog",
        user: discord.abc.User,
        guild: discord.Guild,
        container: discord.ui.Container,
        *,
        is_member: bool,
        is_muted: bool,
        is_banned: bool,
        has_strikes: bool,
    ):
        self.cog = cog
        self.user = user
        self.guild = guild

        action_buttons = []

        strike_btn = discord.ui.Button(
            label="Strike", style=discord.ButtonStyle.danger
        )
        strike_btn.callback = self._strike
        action_buttons.append(strike_btn)

        if is_member:
            kick_btn = discord.ui.Button(
                label="Kick", style=discord.ButtonStyle.danger
            )
            kick_btn.callback = self._kick
            action_buttons.append(kick_btn)

        if is_muted:
            mute_btn = discord.ui.Button(
                label="Unmute", style=discord.ButtonStyle.success
            )
            mute_btn.callback = self._unmute
        else:
            mute_btn = discord.ui.Button(
                label="Mute", style=discord.ButtonStyle.danger
            )
            mute_btn.callback = self._mute
        action_buttons.append(mute_btn)

        if is_banned:
            ban_btn = discord.ui.Button(
                label="Unban", style=discord.ButtonStyle.success
            )
            ban_btn.callback = self._unban
        else:
            ban_btn = discord.ui.Button(
                label="Ban", style=discord.ButtonStyle.danger
            )
            ban_btn.callback = self._ban
        action_buttons.append(ban_btn)

        if has_strikes:
            pardon_btn = discord.ui.Button(
                label="Pardon", style=discord.ButtonStyle.success
            )
            pardon_btn.callback = self._pardon
            action_buttons.append(pardon_btn)

        action_row = discord.ui.ActionRow(*action_buttons)

        refresh_btn = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary)
        refresh_btn.callback = self._refresh
        avatar_btn = discord.ui.Button(label="Avatar", style=discord.ButtonStyle.secondary)
        avatar_btn.callback = self._avatar
        moderations_btn = discord.ui.Button(
            label="Moderations", style=discord.ButtonStyle.secondary
        )
        moderations_btn.callback = self._moderations
        info_row = discord.ui.ActionRow(refresh_btn, avatar_btn, moderations_btn)

        super().__init__(container, action_row, info_row, timeout=180)

    @staticmethod
    async def _check_perm(interaction: discord.Interaction, permission: str) -> bool:
        if not getattr(interaction.user.guild_permissions, permission):
            await interaction.response.send_message(
                "You don't have permission to do that.", ephemeral=True
            )
            return False
        return True

    def _moderation_cog(self):
        return self.cog.moderation_cog()

    async def _strike(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "moderate_members"):
            return
        await interaction.response.send_modal(strike.StrikeModal(self._moderation_cog(), self.user))

    async def _kick(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "kick_members"):
            return
        await interaction.response.send_modal(kick.KickModal(self._moderation_cog(), self.user))

    async def _mute(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "moderate_members"):
            return
        await interaction.response.send_modal(mute.MuteModal(self._moderation_cog(), self.user))

    async def _unmute(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "moderate_members"):
            return
        await interaction.response.send_modal(unmute.UnmuteModal(self._moderation_cog(), self.user))

    async def _ban(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "ban_members"):
            return
        await interaction.response.send_modal(ban.BanModal(self._moderation_cog(), self.user))

    async def _unban(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "ban_members"):
            return
        await interaction.response.send_modal(unban.UnbanModal(self._moderation_cog(), self.user))

    async def _pardon(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "moderate_members"):
            return
        await interaction.response.send_modal(pardon.PardonModal(self._moderation_cog(), self.user))

    async def _refresh(self, interaction: discord.Interaction) -> None:
        view = await self.cog.build_view(self.user, self.guild)
        await interaction.response.edit_message(view=view)

    async def _avatar(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(view=build_avatar_view(self.user, self.guild, True))

    async def _moderations(self, interaction: discord.Interaction) -> None:
        if not await self._check_perm(interaction, "moderate_members"):
            return
        records = await models.list_for_user(self.cog.db, self.guild.id, self.user.id)
        await interaction.response.send_message(view=ModerationListView(records, self.user), ephemeral=True)


class UserInfoCog(DakarCog, name="UserInfo"):
    def moderation_cog(self):
        cog = self.bot.get_cog("Moderation")
        assert cog is not None, "Moderation cog must be loaded for /user's action buttons to work"
        return cog

    async def build_view(self, user: discord.abc.User, guild: discord.Guild) -> UserInfoView:
        member = guild.get_member(user.id)

        details = [
            f"**Discord ID:** {user.id}",
            f"**Created:** <t:{int(user.created_at.timestamp())}:D>",
        ]
        if member is not None and member.joined_at:
            details.append(f"**Joined:** <t:{int(member.joined_at.timestamp())}:D>")

        moderations = await models.list_for_user(self.db, guild.id, user.id)
        strike_sum = await models.get_strike_sum(self.db, guild.id, user.id)
        details.append(f"**Moderations:** {len(moderations)}")
        details.append(f"**Strike count:** {strike_sum}")

        color = discord.Color.green()

        mute_info = await _get_mute_info(self.db, guild, member if member is not None else user)
        is_muted = mute_info is not None
        if mute_info is not None:
            reason, comment, end = mute_info
            details.append(f"**Muted:** {reason}")
            if end:
                details.append(f"**Muted until:** <t:{end}:f>")
            if comment:
                details.append(f"**Mute comment:** {comment}")
            color = discord.Color.orange()

        ban_info = await _get_ban_info(self.db, guild, user)
        is_banned = ban_info is not None
        if ban_info is not None:
            reason, comment, end = ban_info
            details.append(f"**Banned:** {reason}")
            if end:
                details.append(f"**Banned until:** <t:{end}:f>")
            if comment:
                details.append(f"**Ban comment:** {comment}")
            color = discord.Color.red()

        section = discord.ui.Section(
            discord.ui.TextDisplay(f"## {discord.utils.escape_markdown(user.display_name)}"),
            discord.ui.TextDisplay("\n".join(details)),
            accessory=discord.ui.Thumbnail(url=user.display_avatar.url),
        )
        container = discord.ui.Container(section, color=color)

        return UserInfoView(
            self, user, guild, container,
            is_member=member is not None, is_muted=is_muted, is_banned=is_banned, has_strikes=strike_sum > 0,
        )

    # ---- /avatar ----

    @discord.slash_command(name="avatar", description="Show the avatar of a user.", **EVERYWHERE)
    async def avatar(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.User, description="Whose avatar to view", required=False, default=None),
        use_server_profile: discord.Option(
            bool,
            description="Show server-profile avatar if it exists (default: true)",
            name="use-server-profile",
            required=False,
            default=True,
        ),
    ) -> None:
        target = user or ctx.author
        await ctx.respond(view=build_avatar_view(target, ctx.guild, use_server_profile))

    # ---- /user ----

    @discord.slash_command(
        name="user", description="Show information about a user.", default_member_permissions=VIEW_AUDIT_LOG_PERMS
    )
    async def user_info(
        self, ctx: discord.ApplicationContext, user: discord.Option(discord.User, description="Who to look up")
    ) -> None:
        view = await self.build_view(user, ctx.guild)
        await ctx.respond(view=view, ephemeral=True)

    @discord.user_command(name="User Info", default_member_permissions=VIEW_AUDIT_LOG_PERMS)
    async def user_info_context_menu(self, ctx: discord.ApplicationContext, member: discord.Member) -> None:
        view = await self.build_view(member, ctx.guild)
        await ctx.respond(view=view, ephemeral=True)


def setup(bot: DakarBot) -> None:
    bot.add_cog(UserInfoCog(bot))
