"""Implementation for /moderation clear."""
from typing import TYPE_CHECKING

import discord

from dakarbot.moderation import confirmation, models

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(cog: "ModerationCog", ctx: discord.ApplicationContext, user: discord.User) -> None:
    count = await models.count_for_user(cog.db, ctx.guild.id, user.id)
    if count == 0:
        await ctx.respond(f"{user.mention} has no moderations to clear.", ephemeral=True)
        return

    container = discord.ui.Container(
        discord.ui.TextDisplay("**Clear all moderations?**"),
        discord.ui.TextDisplay(
            f"This will permanently delete all {count} moderation(s) for {user.mention}."
        ),
        color=discord.Color.red(),
    )
    record = await models.create_confirmation(cog.db, {"guild_id": ctx.guild.id, "user_id": user.id})

    async def do_clear() -> None:
        deleted = await models.clear_for_user(cog.db, ctx.guild.id, user.id)
        await ctx.respond(f"Deleted {deleted} moderation(s) for {user.mention}.", ephemeral=True)

    view = confirmation.ConfirmationView(cog.db, record, do_clear, container, danger=True)
    await ctx.respond(view=view, ephemeral=True)
    view.message = await ctx.interaction.original_response()
