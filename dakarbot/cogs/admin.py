"""Admin cog: /export and /import for migrating a guild's data between DakarBot,
ModBot, and Vortex instances. Ports modbot's src/commands/bot/{Export,Import}
Command.js — InfoCommand/HelpCenterCommand/PlaylistCommand are explicitly out of
scope for this port (not useful here).

See dakarbot/data_transfer.py for the actual export/import logic and its format
notes (why /export emits modbot's own JSON format rather than a separate one).

Deliberately NOT using `from __future__ import annotations`: see moderation/base.py's
module docstring for why (pycord reads discord.Option(...) off raw annotations).
"""
import gzip
import io
import json
import logging

import discord
from discord.ext import commands

from .. import data_transfer
from ..bot import DakarBot, DakarCog
from ..ui import LayoutView

logger = logging.getLogger("dakarbot.admin")

MANAGE_GUILD_PERMS = discord.Permissions(manage_guild=True)


class AdminCog(DakarCog, name="Admin"):
    @discord.slash_command(
        name="export", description="Export all data DakarBot stores about this guild.",
        default_member_permissions=MANAGE_GUILD_PERMS,
    )
    @commands.cooldown(1, 60, commands.BucketType.guild)
    async def export(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        data = await data_transfer.export_guild(self.bot, ctx.guild.id)
        raw = json.dumps(data, indent=2).encode("utf-8")

        filename = f"DakarBot-data-{ctx.guild.id}.json"
        if len(raw) > ctx.guild.filesize_limit:
            raw = gzip.compress(raw)
            filename += ".gz"

        if len(raw) > ctx.guild.filesize_limit:
            await ctx.respond("Unable to upload exported data (file too large)!", ephemeral=True)
            return

        await ctx.respond(
            file=discord.File(io.BytesIO(raw), filename=filename),
            ephemeral=True,
        )

    @discord.slash_command(
        name="import", description="Import data exported by DakarBot, ModBot, or Vortex.",
        default_member_permissions=MANAGE_GUILD_PERMS,
    )
    @commands.cooldown(1, 60, commands.BucketType.guild)
    async def import_(
        self,
        ctx: discord.ApplicationContext,
        data: discord.Option(discord.Attachment, description="Data exported from DakarBot, ModBot, or Vortex"),
    ) -> None:
        await ctx.defer(ephemeral=True)

        filename = data.filename
        raw = await data.read()
        if filename.endswith(".gz"):
            try:
                raw = gzip.decompress(raw)
            except OSError:
                await ctx.respond(
                    "Failed to decompress gzip data. Make sure the file you're trying to upload is valid gzip.",
                    ephemeral=True,
                )
                return
            filename = filename[:-3]

        if not filename.endswith(".json"):
            await ctx.respond("Unknown file ending. Only .json and .json.gz files are supported.", ephemeral=True)
            return

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await ctx.respond(
                "Failed to parse JSON data. Make sure the file you're trying to upload is valid JSON.",
                ephemeral=True,
            )
            return

        fmt = data_transfer.detect_format(parsed)
        if fmt is None:
            await ctx.respond(
                "Unknown data type. Only ModBot and Vortex data are currently supported.", ephemeral=True
            )
            return

        try:
            if fmt == "modbot":
                result = await data_transfer.import_modbot_data(self.bot, ctx.guild, parsed)
            else:
                result = await data_transfer.import_vortex_data(self.bot, ctx.guild, parsed)
        except Exception:
            logger.exception("Failed to import %s data for guild %s", fmt, ctx.guild.id)
            await ctx.respond("Failed to import this data — check the bot's logs for details.", ephemeral=True)
            return

        lines = "\n".join(f"**{label}:** {count}" for label, count in result.counts.items())
        container = discord.ui.Container(
            discord.ui.TextDisplay("**Imported Data**"), discord.ui.TextDisplay(lines), color=discord.Color.green()
        )
        await ctx.respond(view=LayoutView(container), ephemeral=True)


def setup(bot: DakarBot) -> None:
    bot.add_cog(AdminCog(bot))
