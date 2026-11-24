"""Implementation for /moderation show."""
from typing import TYPE_CHECKING

import discord

from dakarbot.moderation import models
from dakarbot.moderation.models import build_case_container
from dakarbot.ui import LayoutView

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(cog: "ModerationCog", ctx: discord.ApplicationContext, id: int) -> None:
    record = await models.get(cog.db, ctx.guild.id, id)
    if record is None:
        await ctx.respond(f"No moderation with id #{id} found.", ephemeral=True)
        return

    delete_button = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)

    async def _delete(interaction: discord.Interaction) -> None:
        await models.delete(cog.db, ctx.guild.id, id)
        await interaction.response.edit_message(
            view=LayoutView(discord.ui.TextDisplay(f"Deleted moderation #{id}."))
        )

    delete_button.callback = _delete
    view = LayoutView(build_case_container(record), discord.ui.ActionRow(delete_button), timeout=180)
    await ctx.respond(view=view, ephemeral=True)
