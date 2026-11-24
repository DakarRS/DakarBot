"""/about: bot info, latency and links. Ports exorium's stats/ping/invite/support
commands as one panel (there's no privacy policy yet, so no Privacy button)."""
import platform
import time

import discord
import psutil

from .. import settings
from ..about import build_invite_url, build_user_install_url
from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..ui import LayoutView


def _format_uptime(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = [f"{days}d"] if days else []
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


class AboutCog(DakarCog, name="About"):
    def __init__(self, bot: DakarBot):
        super().__init__(bot)
        self._process = psutil.Process()
        self._process.cpu_percent()  # first call only primes the counter

    @discord.slash_command(name="about", description="Show information about the bot.", **EVERYWHERE)
    async def about(self, ctx: discord.ApplicationContext) -> None:
        bot = self.bot
        users = sum(g.member_count or 0 for g in bot.guilds)
        commands_count = sum(1 for _ in bot.walk_application_commands())
        memory_mb = self._process.memory_info().rss / (1024 * 1024)

        stats = "\n".join([
            f"**Servers:** {len(bot.guilds):,}",
            f"**Users:** {users:,}",
            f"**Commands:** {commands_count}",
            f"**Uptime:** {_format_uptime(time.time() - bot.started_at)}",
        ])
        system = "\n".join([
            f"**Latency:** {bot.latency * 1000:.0f} ms",
            f"**Memory:** {memory_mb:.0f} MB",
            f"**CPU:** {self._process.cpu_percent():.1f}%",
            f"**Python:** {platform.python_version()}",
            f"**Library:** py-cord {discord.__version__}",
        ])

        heading = discord.ui.TextDisplay(f"## {bot.user.name}\nA moderation and community bot.")
        avatar = discord.ui.Thumbnail(url=bot.user.display_avatar.url)
        container = discord.ui.Container(
            discord.ui.Section(heading, accessory=avatar),
            discord.ui.Separator(),
            discord.ui.TextDisplay(stats),
            discord.ui.Separator(),
            discord.ui.TextDisplay(system),
            color=discord.Color.blurple(),
        )
        links = discord.ui.ActionRow(
            discord.ui.Button(label="Add to server", url=build_invite_url(bot.application_id)),
            discord.ui.Button(label="Add to my apps", url=build_user_install_url(bot.application_id)),
            discord.ui.Button(label="Support", url=settings.SUPPORT_URL),
        )
        await ctx.respond(view=LayoutView(container, links))


def setup(bot: DakarBot) -> None:
    bot.add_cog(AboutCog(bot))
