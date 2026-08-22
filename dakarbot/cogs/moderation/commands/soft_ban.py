"""Implementation for /soft-ban."""
from typing import TYPE_CHECKING, Optional

import discord

from dakarbot.moderation.models import Moderation

from .._util import DEFAULT_DELETE_SECONDS, parse_duration_option

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(
    cog: "ModerationCog",
    ctx: discord.ApplicationContext,
    user: discord.Member,
    reason: str,
    comment: Optional[str],
    delete: Optional[str],
) -> None:
    try:
        delete_seconds = parse_duration_option(delete) or DEFAULT_DELETE_SECONDS
    except ValueError as exc:
        await ctx.respond(str(exc), ephemeral=True)
        return

    async def perform() -> Optional[Moderation]:
        moderated = cog._moderated_member(ctx, user)
        return await moderated.softban(reason, comment, ctx.author.id, delete_seconds)

    await cog._run_guarded(ctx, user, "soft-ban", perform, danger=True)
