"""Command error handling: a friendly reply for the user, a detailed log for us.

The log record is what reaches the error channel (see error_reporting), so expected
problems like missing permissions or cooldowns are only told to the user.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..error_reporting import describe_interaction
from ..ui import INTERACTION_FAILED, Icon

logger = logging.getLogger("dakarbot.errors")


def _command_context(ctx: discord.ApplicationContext) -> str:
    name = ctx.command.qualified_name if ctx.command else "unknown command"
    options = ", ".join(f"{o['name']}={o.get('value')!r}" for o in (ctx.selected_options or []))
    text = f"`/{name}` {describe_interaction(ctx.interaction)}"
    if options:
        text += f"\nOptions: `{options[:500]}`"
    return text


class ErrorsCog(DakarCog, name="Errors"):
    @commands.Cog.listener()
    async def on_application_command_error(
        self, ctx: discord.ApplicationContext, error: discord.DiscordException
    ) -> None:
        original = getattr(error, "original", error)

        if isinstance(error, commands.CommandOnCooldown):
            await self._reply(ctx, f"⏳ Slow down! Try again in {error.retry_after:.0f}s.")
        elif isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            await self._reply(ctx, f"{Icon.ERROR} I'm missing these permissions here: {missing}")
            logger.warning("Missing permissions (%s) for %s", missing, _command_context(ctx))
        elif isinstance(error, (commands.CheckFailure, discord.CheckFailure)):
            await self._reply(ctx, f"{Icon.ERROR} You can't use this command here.")
        elif isinstance(original, discord.Forbidden):
            await self._reply(ctx, f"{Icon.ERROR} I don't have permission to do that here.")
            logger.warning("Forbidden (%s) in %s", original.text or original.status, _command_context(ctx))
        else:
            await self._reply(ctx, INTERACTION_FAILED)
            logger.error("Command failed: %s", _command_context(ctx), exc_info=original)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # Mentioning the bot is parsed as a text command; there are none, so ignore it.
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error("Text command failed: %s", ctx.message.content[:200], exc_info=error)

    @staticmethod
    async def _reply(ctx: discord.ApplicationContext, text: str) -> None:
        try:
            await ctx.respond(text, ephemeral=True)
        except discord.HTTPException:
            pass  # interaction expired; the log still goes out


def setup(bot: DakarBot) -> None:
    bot.add_cog(ErrorsCog(bot))
