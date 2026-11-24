"""/action (hug, pat, blush, ...) and the owner-only /gifs table behind it. Ports
exorium's 20-odd social commands as a single command."""
import discord
from discord.ext import commands

from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..social.actions import ACTIONS, ActionError, describe
from ..social.gifs import GifStore, is_media_url
from ..ui import LayoutView

# Hidden from members by default; is_owner() is the real gate.
ADMINISTRATOR = discord.Permissions(administrator=True)
ACTION_CHOICES = sorted(ACTIONS)
GIF_LIST_LIMIT = 40


class SocialCog(DakarCog, name="Social"):
    @property
    def gifs(self) -> GifStore:
        return GifStore(self.db)

    @discord.slash_command(name="action", description="Hug, pat, boop and more, with a GIF.", **EVERYWHERE)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def action(
        self,
        ctx: discord.ApplicationContext,
        type: discord.Option(str, description="What to do", choices=ACTION_CHOICES),
        member: discord.Option(discord.User, description="Who to do it to", required=False, default=None),
        reason: discord.Option(str, description="Why", required=False, default=None, max_length=256),
    ) -> None:
        action = ACTIONS[type]
        try:
            text = describe(action, ctx.author.display_name, member.display_name if member else None, reason)
        except ActionError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        items: list[discord.ui.Item] = [discord.ui.TextDisplay(text)]
        url = await self.gifs.random_url(action.name)
        if url:
            items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(url)))
        await ctx.respond(view=LayoutView(discord.ui.Container(*items, color=discord.Color.blurple())))

    gifs_group = discord.SlashCommandGroup(
        "gifs", "Manage the GIFs used by /action.", default_member_permissions=ADMINISTRATOR
    )

    @gifs_group.command(name="add", description="Add a GIF to an action.")
    @commands.is_owner()
    async def gifs_add(
        self,
        ctx: discord.ApplicationContext,
        type: discord.Option(str, description="Which action", choices=ACTION_CHOICES),
        url: discord.Option(str, description="Direct link to the GIF or image"),
    ) -> None:
        await ctx.defer(ephemeral=True)
        url = url.strip()
        if not await is_media_url(self.http_session, url):
            await ctx.respond("That link didn't load as an image or video. Use a direct link.", ephemeral=True)
            return
        gif = await self.gifs.add(type, url)
        if gif is None:
            await ctx.respond(f"{type} already has that GIF.", ephemeral=True)
            return
        await ctx.respond(f"Added GIF #{gif.id} to {type}.", ephemeral=True)

    @gifs_group.command(name="remove", description="Remove a GIF by its number from /gifs list.")
    @commands.is_owner()
    async def gifs_remove(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="The GIF's number", min_value=1),
    ) -> None:
        removed = await self.gifs.remove(id)
        await ctx.respond(f"Removed GIF #{id}." if removed else f"There's no GIF #{id}.", ephemeral=True)

    @gifs_group.command(name="list", description="List the GIFs for an action.")
    @commands.is_owner()
    async def gifs_list(
        self,
        ctx: discord.ApplicationContext,
        type: discord.Option(str, description="Which action", choices=ACTION_CHOICES),
    ) -> None:
        gifs = await self.gifs.list(type)
        if not gifs:
            await ctx.respond(f"{type} has no GIFs yet.", ephemeral=True)
            return
        lines = [f"**#{g.id}** <{g.url}>" for g in gifs[:GIF_LIST_LIMIT]]
        if len(gifs) > GIF_LIST_LIMIT:
            lines.append(f"-# and {len(gifs) - GIF_LIST_LIMIT} more")
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## {type} GIFs ({len(gifs)})"),
            discord.ui.TextDisplay("\n".join(lines)[:3900]),
            color=discord.Color.blurple(),
        )
        await ctx.respond(view=LayoutView(container), ephemeral=True)


def setup(bot: DakarBot) -> None:
    bot.add_cog(SocialCog(bot))
