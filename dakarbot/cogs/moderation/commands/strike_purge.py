"""Implementation for /strike-purge."""
from typing import TYPE_CHECKING, Optional

import discord

from dakarbot.moderation.models import Moderation

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(
    cog: "ModerationCog",
    ctx: discord.ApplicationContext,
    user: discord.Member,
    reason: str,
    comment: Optional[str],
    count: int,
    limit: int,
) -> None:
    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        moderation = await moderated.strike(reason, comment, ctx.author.id, amount=count)

        to_delete = [msg async for msg in ctx.channel.history(limit=limit) if msg.author.id == user.id]
        if to_delete:
            await ctx.channel.delete_messages(to_delete)
            await ctx.followup.send(
                f"Deleted {len(to_delete)} message(s) from {user.mention}.", ephemeral=True
            )
        return moderation

    await cog._run_guarded(ctx, user, "strike", perform)
