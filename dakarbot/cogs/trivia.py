"""/trivia: one multiple-choice question with answer buttons. Correct answers pay out
in servers the bot is in; elsewhere (as a user app) it's just for fun."""
import asyncio
import random
from typing import Optional

import discord
from discord.ext import commands

from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..economy.balances import Balances
from ..trivia import TRIVIA_REWARDS, Question, fetch_question
from ..ui import LayoutView

ANSWER_SECONDS = 20
LABEL_LIMIT = 80
_rng = random.SystemRandom()


class TriviaView(LayoutView):
    def __init__(self, question: Question, user_id: int, on_correct):
        self.question, self._user_id, self._on_correct = question, user_id, on_correct
        self._lock = asyncio.Lock()
        self._answered = False
        self.message: discord.Message | discord.WebhookMessage | None = None
        super().__init__(timeout=ANSWER_SECONDS)
        self._render()

    def _render(self, picked: Optional[str] = None, footer: str = "") -> None:
        q = self.question
        text = f"## Trivia\n-# {discord.utils.escape_markdown(q.category)} - {q.difficulty}\n" \
               f"{discord.utils.escape_markdown(q.text)}"
        if footer:
            text += f"\n\n{footer}"
        else:
            text += f"\n-# You have {ANSWER_SECONDS} seconds."
        buttons = []
        for answer in q.answers:
            style = discord.ButtonStyle.secondary
            if self._answered or footer:
                if answer == q.correct:
                    style = discord.ButtonStyle.success
                elif answer == picked:
                    style = discord.ButtonStyle.danger
            button = discord.ui.Button(label=answer[:LABEL_LIMIT], style=style, disabled=bool(footer))
            button.callback = self._answer(answer)
            buttons.append(button)
        self.clear_items()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text), color=discord.Color.blurple()))
        self.add_item(discord.ui.ActionRow(*buttons))

    def _answer(self, answer: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._user_id:
                await interaction.response.send_message("Start your own with /trivia.", ephemeral=True)
                return
            async with self._lock:
                if self._answered:
                    await interaction.response.defer()
                    return
                self._answered = True
                self.stop()
                if answer == self.question.correct:
                    footer = "**Correct!**" + (await self._on_correct() or "")
                else:
                    footer = f"**Wrong.** The answer was **{discord.utils.escape_markdown(self.question.correct)}**."
                self._render(answer, footer)
                await interaction.response.edit_message(view=self)
        return callback

    async def on_timeout(self) -> None:
        async with self._lock:
            if self._answered:
                return
            self._answered = True
            self._render(footer=f"**Time's up.** The answer was "
                                f"**{discord.utils.escape_markdown(self.question.correct)}**.")
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class TriviaCog(DakarCog, name="Trivia"):
    @discord.slash_command(name="trivia", description="Answer a trivia question. Get it right to earn currency.",
                           **EVERYWHERE)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def trivia(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer()
        question = await fetch_question(self.http_session, _rng)
        if question is None:
            ctx.command.reset_cooldown(ctx)
            await ctx.respond("I couldn't get a question right now. Try again in a few seconds.", ephemeral=True)
            return
        # Balances are per server, so only pay out in servers the bot is actually in.
        guild = self.bot.get_guild(ctx.guild_id) if ctx.guild_id else None
        user_id = ctx.author.id

        async def on_correct() -> str:
            if guild is None:
                return ""
            reward, balance = await Balances(self.db).add(guild.id, user_id, TRIVIA_REWARDS[question.difficulty])
            name = await self.guild_settings.get_currency_name(guild.id)
            return f" You earned **{reward:,}** {name}.\n-# Balance: {balance:,} {name}"

        view = TriviaView(question, user_id, on_correct)
        view.message = await ctx.respond(view=view)


def setup(bot: DakarBot) -> None:
    bot.add_cog(TriviaCog(bot))
