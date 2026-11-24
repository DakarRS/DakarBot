"""/fox and /pokemon, ported from exorium's social cog."""
from typing import Optional

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..fun_apis import fetch_fox
from ..pokemon import PokeApi, PokemonInfo
from ..ui import LayoutView


def _pokemon_view(info: PokemonInfo) -> LayoutView:
    title = f"## {info.display_name}"
    if info.genus:
        title += f"\n{info.genus}"
    details = [
        f"**Type:** {', '.join(info.types)}",
        f"**Abilities:** {', '.join(info.abilities)}",
        f"**Egg groups:** {', '.join(info.egg_groups) or 'None'}",
        f"**Evolution:** {' → '.join(info.evolution_line)} (stage {info.evolution_stage})",
    ]
    stats = "\n".join(f"**{label}:** {value}" for label, value in info.stats.items())
    heading = discord.ui.TextDisplay(title)
    top = (
        discord.ui.Section(heading, accessory=discord.ui.Thumbnail(url=info.artwork_url))
        if info.artwork_url else heading
    )
    items: list[discord.ui.Item] = [top]
    if info.description:
        items.append(discord.ui.TextDisplay(info.description))
    items += [
        discord.ui.Separator(),
        discord.ui.TextDisplay("\n".join(details)),
        discord.ui.Separator(),
        discord.ui.TextDisplay(stats),
    ]
    links = discord.ui.ActionRow(discord.ui.Button(label="Bulbapedia", url=info.bulbapedia_url))
    return LayoutView(discord.ui.Container(*items, color=discord.Color.red()), links)


class FunCog(DakarCog, name="Fun"):
    def __init__(self, bot: DakarBot):
        super().__init__(bot)
        self._pokeapi: Optional[PokeApi] = None

    @property
    def pokeapi(self) -> PokeApi:
        # Created lazily: the HTTP session only exists once the bot has started.
        if self._pokeapi is None:
            self._pokeapi = PokeApi(self.http_session)
        return self._pokeapi

    async def _pokemon_autocomplete(self, ctx: discord.AutocompleteContext) -> list[str]:
        return await self.pokeapi.search_names(ctx.value or "")

    @discord.slash_command(name="fox", description="Get a random fox picture.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fox(self, ctx: discord.ApplicationContext) -> None:
        url = await fetch_fox(self.http_session)
        if url is None:
            await ctx.respond("The fox service isn't responding right now. Try again later.", ephemeral=True)
            return
        container = discord.ui.Container(
            discord.ui.MediaGallery(discord.MediaGalleryItem(url)), color=discord.Color.orange()
        )
        await ctx.respond(view=LayoutView(container))

    @discord.slash_command(name="pokemon", description="Look up a Pokémon.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pokemon(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, description="The Pokémon's name", autocomplete=_pokemon_autocomplete),
    ) -> None:
        await ctx.defer()
        info = await self.pokeapi.fetch(name)
        if info is None:
            await ctx.respond(f"Couldn't find a Pokémon called \"{name}\".", ephemeral=True)
            return
        await ctx.respond(view=_pokemon_view(info))


def setup(bot: DakarBot) -> None:
    bot.add_cog(FunCog(bot))
