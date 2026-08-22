"""Implementation for /pardon and its Pardon modal (only ever opened from the /user
info panel's action row — modbot's PardonCommand doesn't support a Discord user
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
    count: int,
) -> None:
    moderated = cog._moderated_member(ctx, user)
    available = await moderated.get_strike_sum()
    clamped = min(count, max(available, 0))
    if clamped <= 0:
        await ctx.respond(f"{user.mention} has no strikes to pardon.", ephemeral=True)
        return

    async def perform() -> Optional[Moderation]:
        return await moderated.pardon(reason, comment, ctx.author.id, amount=clamped)

    await cog._run_guarded(ctx, user, "pardon", perform)


class PardonModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.Member):
        super().__init__(cog, target, "pardon")
        self.count_input = discord.ui.InputText(label="Strike count to pardon (default 1)", required=False)
        self.add_item(self.count_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        try:
            count = max(1, min(100, int(self.count_input.value))) if self.count_input.value else 1
        except ValueError:
            await interaction.response.send_message("Strike count must be a whole number.", ephemeral=True)
            return
        await run(self.cog, ctx, self.target, self.reason, self.comment, count)
