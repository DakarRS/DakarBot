"""Bot-wide background behavior ported from exorium's event cogs: the blacklist gate,
join/leave notices, and the command usage log."""
import logging
from typing import Optional

import discord
from discord.ext import commands

from .. import settings
from ..blacklist import BlacklistKind
from ..bot import DakarBot, DakarCog
from ..error_reporting import describe_guild
from ..ui import LayoutView
from ._guild_summary import guild_summary_container

logger = logging.getLogger("dakarbot.bot_events")


class Blacklisted(discord.CheckFailure):
    """The errors cog answers this with a private "you're blocked" notice."""


class BotEventsCog(DakarCog, name="BotEvents"):
    async def bot_check(self, ctx: discord.ApplicationContext) -> bool:
        if self.blacklist.is_blacklisted(BlacklistKind.USER, ctx.author.id) or self.blacklist.is_blacklisted(
            BlacklistKind.SERVER, ctx.guild_id
        ):
            raise Blacklisted()
        return True

    async def _channel(self, channel_id: Optional[int]) -> Optional[discord.abc.Messageable]:
        if channel_id is None:
            return None
        try:
            return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            logger.warning("Couldn't open notice channel %s", channel_id)
            return None

    async def _notice(self, container: discord.ui.Container) -> None:
        channel = await self._channel(settings.ERROR_CHANNEL_ID)
        if channel is None:
            return
        try:
            await channel.send(view=LayoutView(container), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Couldn't post a server join/leave notice")

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.blacklist.is_blacklisted(BlacklistKind.SERVER, guild.id):
            await guild.leave()
            footer = "Blacklisted, so I left right away"
            await self._notice(guild_summary_container(guild, "Blacklisted server added me", discord.Color.dark_red(), footer))
            return
        footer = f"Now in {len(self.bot.guilds):,} servers"
        await self._notice(guild_summary_container(guild, "Joined a server", discord.Color.green(), footer))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if self.blacklist.is_blacklisted(BlacklistKind.SERVER, guild.id):
            return  # the blacklist flow already reported it
        footer = f"Now in {len(self.bot.guilds):,} servers"
        await self._notice(guild_summary_container(guild, "Left a server", discord.Color.red(), footer))

    @commands.Cog.listener()
    async def on_application_command(self, ctx: discord.ApplicationContext) -> None:
        channel = await self._channel(settings.COMMAND_LOG_CHANNEL_ID)
        if channel is None:
            return
        where = describe_guild(ctx.guild) + (f" <#{ctx.channel_id}>" if ctx.guild else "")
        text = f"`/{ctx.command.qualified_name}` by {ctx.author.mention} (`{ctx.author.id}`) in {where}"
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Couldn't post to the command log channel")


def setup(bot: DakarBot) -> None:
    bot.add_cog(BotEventsCog(bot))
