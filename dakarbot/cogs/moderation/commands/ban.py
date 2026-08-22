"""Implementation for /ban and its "Ban" context-menu modal. See base.py for the
@discord.slash_command declaration (pycord needs the Option-annotated signature on the
cog method itself)."""
from typing import TYPE_CHECKING, Optional

import discord

from dakarbot.moderation.models import Moderation

from .._action_modal import ActionModal
from .._util import DEFAULT_DELETE_SECONDS, parse_duration_option

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(
    cog: "ModerationCog",
    ctx: discord.ApplicationContext,
    user: discord.Member,
    reason: str,
    comment: Optional[str],
    duration: Optional[str],
    delete: Optional[str],
) -> None:
    try:
        duration_seconds = parse_duration_option(duration)
        delete_seconds = parse_duration_option(delete) or DEFAULT_DELETE_SECONDS
    except ValueError as exc:
        await ctx.respond(str(exc), ephemeral=True)
        return

    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.ban(reason, comment, ctx.author.id, duration_seconds, delete_seconds)

    await cog._run_guarded(ctx, user, "ban", perform, danger=True)


class BanModal(ActionModal):
    def __init__(self, cog: "ModerationCog", target: discord.Member):
        super().__init__(cog, target, "ban")
        self.duration_input = discord.ui.InputText(
            label="Duration (e.g. '1d'); empty = permanent", required=False
        )
        self.delete_input = discord.ui.InputText(
            label="Delete message history (e.g. '1h')", required=False
        )
        self.add_item(self.duration_input)
        self.add_item(self.delete_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.application_context(interaction)
        await run(
            self.cog, ctx, self.target, self.reason, self.comment,
            self.duration_input.value, self.delete_input.value,
        )
