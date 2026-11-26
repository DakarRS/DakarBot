"""/profile: balance, level, streak, top action and inventory on one card."""
import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..economy.daily import utc_today
from ..leveling.curve import progress, progress_bar
from ..profile import load_profile
from ..ui import LayoutView

THUMBNAIL_SIZE = 256


def _ranked(text: str, rank) -> str:
    return f"{text} (#{rank})" if rank else text


class ProfileCog(DakarCog, name="Profile"):
    @discord.slash_command(name="profile", description="Show your profile, or someone else's.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def profile(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Whose profile to show", required=False, default=None),
    ) -> None:
        member = member or ctx.author
        p = await load_profile(self.db, ctx.guild.id, member.id, utc_today())
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        _, into, needed = progress(p.xp)
        lines = [
            f"**Balance:** {_ranked(f'{p.balance:,} {name}', p.balance_rank)}",
            f"**Level:** {_ranked(str(p.level), p.level_rank)}  `{progress_bar(into, needed)}` {into:,} / {needed:,} XP",
            f"**Daily streak:** {p.streak} day{'s' if p.streak != 1 else ''}",
            f"**Favorite action:** {f'{p.top_action[0]} (x{p.top_action[1]:,})' if p.top_action else 'None yet'}",
            f"**Items:** {p.items:,}",
        ]
        header = discord.ui.Section(
            discord.ui.TextDisplay(f"## {discord.utils.escape_markdown(member.display_name)}"),
            discord.ui.TextDisplay("\n".join(lines)),
            accessory=discord.ui.Thumbnail(member.display_avatar.with_size(THUMBNAIL_SIZE).url),
        )
        color = member.color if member.color.value else discord.Color.blurple()
        await ctx.respond(view=LayoutView(discord.ui.Container(header, color=color)))


def setup(bot: DakarBot) -> None:
    bot.add_cog(ProfileCog(bot))
