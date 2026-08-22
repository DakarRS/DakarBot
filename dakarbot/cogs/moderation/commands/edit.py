"""Implementation for /moderation edit."""
from typing import TYPE_CHECKING, Optional

import discord

from dakarbot.moderation import models
from dakarbot.moderation.models import ModerationAction, build_case_view

from .._util import parse_duration_option

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(
    cog: "ModerationCog",
    ctx: discord.ApplicationContext,
    id: int,
    reason: Optional[str],
    comment: Optional[str],
    duration: Optional[str],
    count: Optional[int],
) -> None:
    if reason is None and comment is None and duration is None and count is None:
        await ctx.respond("You must provide at least one field to edit.", ephemeral=True)
        return

    record = await models.get(cog.db, ctx.guild.id, id)
    if record is None:
        await ctx.respond(f"No moderation with id #{id} found.", ephemeral=True)
        return

    if reason is not None:
        record.reason = reason
    if comment is not None:
        record.comment = comment
    if duration is not None:
        if not record.active:
            await ctx.respond("Can't change the duration of an inactive moderation.", ephemeral=True)
            return
        try:
            duration_seconds = parse_duration_option(duration)
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return
        record.expire_time = record.created + duration_seconds if duration_seconds else None
    if count is not None:
        if record.action not in (ModerationAction.STRIKE, ModerationAction.PARDON):
            await ctx.respond("Count can only be edited for strike/pardon cases.", ephemeral=True)
            return
        record.value = -count if record.action is ModerationAction.PARDON else count

    await models.update(cog.db, record)
    await ctx.respond(view=build_case_view(record), ephemeral=True)
