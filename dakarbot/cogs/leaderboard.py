"""/leaderboard: richest members and longest active daily streaks, switchable with a
dropdown that only the person who ran the command can use."""
from typing import Awaitable, Callable

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..economy.daily import utc_today
from ..economy.leaderboard import Ranking, richest, streaks
from ..ui import LayoutView

TIMEOUT_SECONDS = 120
NO_MENTIONS = discord.AllowedMentions.none()


def _line(rank: int, user_id: int, value: str, is_caller: bool) -> str:
    line = f"**#{rank}** <@{user_id}> - {value}"
    return f"{line} (you)" if is_caller else line


def render(title: str, ranking: Ranking, unit: Callable[[int], str], caller_id: int) -> str:
    if not ranking.top:
        return f"## {title}\nNobody's on the board yet."
    lines = [_line(e.rank, e.user_id, unit(e.value), e.user_id == caller_id) for e in ranking.top]
    caller = ranking.caller
    if caller is not None and caller not in ranking.top:
        lines.append(f"\n{_line(caller.rank, caller.user_id, unit(caller.value), True)}")
    return f"## {title}\n" + "\n".join(lines)


def _board(text: str) -> discord.ui.Container:
    return discord.ui.Container(discord.ui.TextDisplay(text), color=discord.Color.gold())


class LeaderboardView(LayoutView):
    def __init__(self, boards: dict[str, Callable[[], Awaitable[str]]], text: str, selected: str, user_id: int):
        self._boards = boards
        self._text = text
        self._user_id = user_id
        self.message: discord.Message | None = None
        select = discord.ui.Select(options=[
            discord.SelectOption(label=label, value=label, default=label == selected) for label in boards
        ])
        select.callback = self._switch
        self._select = select
        super().__init__(
            _board(text),
            discord.ui.ActionRow(select),
            timeout=TIMEOUT_SECONDS,
        )

    async def _switch(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Run /leaderboard to get your own.", ephemeral=True)
            return
        choice = self._select.values[0]
        text = await self._boards[choice]()
        view = LeaderboardView(self._boards, text, choice, self._user_id)
        view.message = self.message
        self.stop()
        await interaction.response.edit_message(view=view, allowed_mentions=NO_MENTIONS)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=LayoutView(_board(self._text)), allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            pass


class LeaderboardCog(DakarCog, name="Leaderboard"):
    @discord.slash_command(name="leaderboard", description="See who's richest and who has the longest daily streak.")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def leaderboard(self, ctx: discord.ApplicationContext) -> None:
        guild_id, caller_id = ctx.guild.id, ctx.author.id
        name = await self.guild_settings.get_currency_name(guild_id)

        async def richest_board() -> str:
            return render("Richest", await richest(self.db, guild_id, caller_id), lambda v: f"{v:,} {name}", caller_id)

        async def streak_board() -> str:
            ranking = await streaks(self.db, guild_id, caller_id, utc_today())
            return render("Daily streaks", ranking, lambda v: f"{v} day{'s' if v != 1 else ''}", caller_id)

        boards = {"Richest": richest_board, "Daily streaks": streak_board}
        view = LeaderboardView(boards, await richest_board(), "Richest", caller_id)
        await ctx.respond(view=view, allowed_mentions=NO_MENTIONS)
        view.message = await ctx.interaction.original_response()


def setup(bot: DakarBot) -> None:
    bot.add_cog(LeaderboardCog(bot))
