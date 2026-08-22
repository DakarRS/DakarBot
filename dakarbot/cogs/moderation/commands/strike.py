"""Implementation for /strike and its "Strike" context-menu modal."""
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
    count: int,
) -> None:
    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.strike(reason, comment, ctx.author.id, amount=count)

    await cog._run_guarded(ctx, user, "strike", perform)


class StrikeModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.Member):
        super().__init__(cog, target, "strike")
        self.count_input = discord.ui.InputText(label="Strike count (default 1)", required=False)
        self.add_item(self.count_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        try:
            count = max(1, min(100, int(self.count_input.value))) if self.count_input.value else 1
        except ValueError:
            await interaction.response.send_message("Strike count must be a whole number.", ephemeral=True)
            return
        await run(self.cog, ctx, self.target, self.reason, self.comment, count)
