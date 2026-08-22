"""Implementation for /unmute and its Unmute modal (only ever opened from the /user
info panel's action row — modbot's UnmuteCommand doesn't support a Discord user
context menu, only /user's button does)."""
from typing import TYPE_CHECKING, Optional

import discord

from dakarbot.moderation.models import Moderation

from .._action_modal import ActionModal

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(
    cog: "ModerationCog",
    ctx: discord.ApplicationContext,
    user: discord.Member,
    reason: str,
    comment: Optional[str],
) -> None:
    muted_role_id = await cog.guild_settings.get_muted_role_id(ctx.guild.id)
    muted_role = ctx.guild.get_role(muted_role_id) if muted_role_id else None
    timed_out_until = user.communication_disabled_until
    is_timed_out = timed_out_until is not None and timed_out_until > discord.utils.utcnow()
    has_muted_role = muted_role is not None and muted_role in user.roles
    if not is_timed_out and not has_muted_role:
        await ctx.respond("This member isn't muted!", ephemeral=True)
        return

    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.unmute(reason, comment, ctx.author.id)

    await cog._run_guarded(ctx, user, "unmute", perform)


class UnmuteModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.Member):
        super().__init__(cog, target, "unmute")

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        await run(self.cog, ctx, self.target, self.reason, self.comment)
