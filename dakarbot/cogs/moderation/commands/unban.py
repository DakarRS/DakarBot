"""Implementation for /unban and its Unban modal (only ever opened from the /user
info panel's action row — modbot's UnbanCommand doesn't support a Discord user
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
    user: discord.User,
    reason: str,
    comment: Optional[str],
) -> None:
    try:
        await ctx.guild.fetch_ban(user)
    except discord.NotFound:
        await ctx.respond("This member isn't banned!", ephemeral=True)
        return

    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.unban(reason, comment, ctx.author.id)

    await cog._run_guarded(ctx, user, "unban", perform)


class UnbanModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.User):
        super().__init__(cog, target, "unban")

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        await run(self.cog, ctx, self.target, self.reason, self.comment)
