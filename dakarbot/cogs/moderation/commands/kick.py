"""Implementation for /kick and its "Kick" context-menu modal."""
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
    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.kick(reason, comment, ctx.author.id)

    await cog._run_guarded(ctx, user, "kick", perform)


class KickModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.Member):
        super().__init__(cog, target, "kick")

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        await run(self.cog, ctx, self.target, self.reason, self.comment)
