"""Implementation for /moderation delete."""
from typing import TYPE_CHECKING

import discord

from dakarbot.moderation import models
from dakarbot.ui import LayoutView

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(cog: "ModerationCog", ctx: discord.ApplicationContext, id: int) -> None:
    record = await models.get(cog.db, ctx.guild.id, id)
    if record is None:
        await ctx.respond(f"No moderation with id #{id} found.", ephemeral=True)
        return
    await models.delete(cog.db, ctx.guild.id, id)
    container = discord.ui.Container(
        discord.ui.TextDisplay(f"## Deleted Case #{id} — {record.action.value.upper()}"),
        color=discord.Color.red(),
    )
    await ctx.respond(view=LayoutView(container), ephemeral=True)
