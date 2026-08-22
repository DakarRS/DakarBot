"""Implementation for /mute and its "Mute" context-menu modal."""
from typing import TYPE_CHECKING, Optional

import discord

from dakarbot.moderation.models import Moderation

from .._action_modal import ActionModal
from .._util import parse_duration_option

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(
    cog: "ModerationCog",
    ctx: discord.ApplicationContext,
    user: discord.Member,
    reason: str,
    comment: Optional[str],
    duration: Optional[str],
) -> None:
    try:
        duration_seconds = parse_duration_option(duration)
    except ValueError as exc:
        await ctx.respond(str(exc), ephemeral=True)
        return

    if error := await cog._mute_precheck(ctx, duration_seconds):
        await ctx.respond(error, ephemeral=True)
        return

    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.mute(reason, comment, ctx.author.id, duration_seconds)

    await cog._run_guarded(ctx, user, "mute", perform)


class MuteModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.Member):
        super().__init__(cog, target, "mute")
        self.duration_input = discord.ui.InputText(
            label="Duration (e.g. '30m', '1d'); empty=permanent", required=False
        )
        self.add_item(self.duration_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        await run(self.cog, ctx, self.target, self.reason, self.comment, self.duration_input.value)
