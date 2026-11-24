"""/owner: bot-owner tools ported from exorium's admin cog (info, blacklist, leave,
shutdown as restart, and a read-only take on sql). The whole cog is owner-only."""
import asyncpg
import discord

from ..blacklist import BlacklistKind
from ..bot import DakarBot, DakarCog
from ..readonly_sql import QueryRejected, format_result, run_readonly
from ..ui import ConfirmView, LayoutView
from ._guild_summary import guild_summary_container, parse_snowflake

# Hidden from members by default; cog_check is the real gate.
ADMINISTRATOR = discord.Permissions(administrator=True)
KIND_CHOICES = [discord.OptionChoice(name=k.value.title(), value=k.value) for k in BlacklistKind]
BLACKLIST_LIST_LIMIT = 50


async def _guild_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    value = (ctx.value or "").lower()
    matches = [g for g in ctx.bot.guilds if value in g.name.lower() or value in str(g.id)]
    return [discord.OptionChoice(name=f"{g.name[:80]} ({g.id})", value=str(g.id)) for g in matches[:25]]


def _confirm_container(title: str, body: str) -> discord.ui.Container:
    return discord.ui.Container(
        discord.ui.TextDisplay(f"## {title}"), discord.ui.TextDisplay(body), color=discord.Color.red()
    )


class OwnerCog(DakarCog, name="Owner"):
    async def cog_check(self, ctx: discord.ApplicationContext) -> bool:
        return await self.bot.is_owner(ctx.author)

    owner = discord.SlashCommandGroup("owner", "Bot owner tools.", default_member_permissions=ADMINISTRATOR)
    info = owner.create_subgroup("info", "Look up any server or user.")
    blacklist_group = owner.create_subgroup("blacklist", "Bar users or servers from the bot.")

    async def _confirm(self, ctx: discord.ApplicationContext, container: discord.ui.Container, action) -> None:
        view = ConfirmView(container, action, danger=True, user_id=ctx.author.id)
        await ctx.respond(view=view, ephemeral=True)
        view.message = await ctx.interaction.original_response()

    @info.command(name="server", description="Show details about a server the bot is in.")
    async def info_server(
        self, ctx: discord.ApplicationContext,
        server: discord.Option(str, description="Server ID", autocomplete=_guild_autocomplete),
    ) -> None:
        guild_id = parse_snowflake(server)
        guild = self.bot.get_guild(guild_id) if guild_id else None
        if guild is None:
            await ctx.respond("I'm not in that server.", ephemeral=True)
            return
        footer = "Blacklisted" if self.blacklist.is_blacklisted(BlacklistKind.SERVER, guild.id) else None
        container = guild_summary_container(guild, "Server info", discord.Color.blurple(), footer)
        await ctx.respond(view=LayoutView(container), ephemeral=True)

    @info.command(name="user", description="Show details about any Discord user.")
    async def info_user(
        self, ctx: discord.ApplicationContext, user: discord.Option(str, description="User ID")
    ) -> None:
        user_id = parse_snowflake(user)
        try:
            found = await self.bot.get_or_fetch_user(user_id) if user_id else None
        except discord.NotFound:
            found = None
        if found is None:
            await ctx.respond("Couldn't find that user.", ephemeral=True)
            return
        mutual = sum(1 for g in self.bot.guilds if g.get_member(found.id))
        lines = [
            f"**ID:** {found.id}",
            f"**Profile:** {found.mention}",
            f"**Bot:** {'Yes' if found.bot else 'No'}",
            f"**Created:** <t:{int(found.created_at.timestamp())}:D>",
            f"**Mutual servers:** {mutual}",
            f"**Blacklisted:** {'Yes' if self.blacklist.is_blacklisted(BlacklistKind.USER, found.id) else 'No'}",
        ]
        heading = discord.ui.TextDisplay(f"## User info\n{discord.utils.escape_markdown(str(found))}")
        container = discord.ui.Container(
            discord.ui.Section(heading, accessory=discord.ui.Thumbnail(url=found.display_avatar.url)),
            discord.ui.TextDisplay("\n".join(lines)),
            color=discord.Color.blurple(),
        )
        await ctx.respond(view=LayoutView(container), ephemeral=True)

    @blacklist_group.command(name="add", description="Blacklist a user or server.")
    async def blacklist_add(
        self, ctx: discord.ApplicationContext,
        kind: discord.Option(str, description="User or server", choices=KIND_CHOICES),
        id: discord.Option(str, description="User or server ID"),
        reason: discord.Option(str, description="Why", required=False, default=None, max_length=500),
    ) -> None:
        target_id = parse_snowflake(id)
        blacklist_kind = BlacklistKind(kind)
        if target_id is None:
            await ctx.respond("That isn't a valid ID.", ephemeral=True)
            return
        if blacklist_kind is BlacklistKind.USER and target_id in self.bot.owner_ids:
            await ctx.respond("You can't blacklist a bot owner.", ephemeral=True)
            return
        if not await self.blacklist.add(blacklist_kind, target_id, reason):
            await ctx.respond(f"That {kind} is already blacklisted.", ephemeral=True)
            return
        text = f"Blacklisted {kind} `{target_id}`."
        guild = self.bot.get_guild(target_id) if blacklist_kind is BlacklistKind.SERVER else None
        if guild is not None:
            await guild.leave()
            text += f" I also left **{discord.utils.escape_markdown(guild.name)}**."
        await ctx.respond(text, ephemeral=True)

    @blacklist_group.command(name="remove", description="Remove a user or server from the blacklist.")
    async def blacklist_remove(
        self, ctx: discord.ApplicationContext,
        kind: discord.Option(str, description="User or server", choices=KIND_CHOICES),
        id: discord.Option(str, description="User or server ID"),
    ) -> None:
        target_id = parse_snowflake(id)
        removed = target_id is not None and await self.blacklist.remove(BlacklistKind(kind), target_id)
        await ctx.respond(
            f"Removed {kind} `{target_id}` from the blacklist." if removed else f"That {kind} isn't blacklisted.",
            ephemeral=True,
        )

    @blacklist_group.command(name="list", description="Show everything on the blacklist.")
    async def blacklist_list(self, ctx: discord.ApplicationContext) -> None:
        entries = await self.blacklist.list()
        if not entries:
            await ctx.respond("The blacklist is empty.", ephemeral=True)
            return
        lines = [
            f"**{e.kind.value.title()}** `{e.target_id}` <t:{e.created}:d>" + (f": {e.reason}" if e.reason else "")
            for e in entries[:BLACKLIST_LIST_LIMIT]
        ]
        if len(entries) > BLACKLIST_LIST_LIMIT:
            lines.append(f"-# and {len(entries) - BLACKLIST_LIST_LIMIT} more")
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## Blacklist ({len(entries)})"),
            discord.ui.TextDisplay("\n".join(lines)[:3900]),
            color=discord.Color.dark_grey(),
        )
        await ctx.respond(view=LayoutView(container), ephemeral=True)

    @owner.command(name="leave", description="Make the bot leave a server.")
    async def leave(
        self, ctx: discord.ApplicationContext,
        server: discord.Option(str, description="Server ID", autocomplete=_guild_autocomplete),
    ) -> None:
        guild_id = parse_snowflake(server)
        guild = self.bot.get_guild(guild_id) if guild_id else None
        if guild is None:
            await ctx.respond("I'm not in that server.", ephemeral=True)
            return
        name = discord.utils.escape_markdown(guild.name)

        async def do_leave() -> None:
            await guild.leave()
            await ctx.followup.send(f"Left **{name}**.", ephemeral=True)

        await self._confirm(ctx, _confirm_container("Leave server?", f"I'll leave **{name}** (`{guild.id}`)."), do_leave)

    @owner.command(name="restart", description="Restart the bot.")
    async def restart(self, ctx: discord.ApplicationContext) -> None:
        async def do_restart() -> None:
            await ctx.followup.send("Restarting...", ephemeral=True)
            await self.bot.close()  # Docker's restart policy starts it back up

        body = "The bot goes offline for a few seconds while Docker brings it back."
        await self._confirm(ctx, _confirm_container("Restart the bot?", body), do_restart)

    @owner.command(name="sql", description="Run a read-only SQL query.")
    async def sql(
        self, ctx: discord.ApplicationContext,
        query: discord.Option(str, description="SELECT, WITH, EXPLAIN, SHOW, VALUES or TABLE", max_length=1500),
    ) -> None:
        await ctx.defer(ephemeral=True)
        try:
            result = await run_readonly(self.db, query)
        except QueryRejected as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        except asyncpg.PostgresError as error:
            await ctx.respond(f"```\n{type(error).__name__}: {str(error)[:1500]}\n```", ephemeral=True)
            return
        await ctx.respond(format_result(result), ephemeral=True)


def setup(bot: DakarBot) -> None:
    bot.add_cog(OwnerCog(bot))
