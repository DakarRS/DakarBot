"""/rps duels for a pot and /rob (off unless an admin turns it on with /currency rob).
Both rps bets are held until the duel ends; a decline, timeout or tie refunds them."""
import asyncio
import random
import time

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..economy.balances import PaymentError
from ..games.rob import RobCooldown, RobError, Robberies, roll_rob
from ..games.rps import CHOICES, rps_payouts
from ..games.wagers import BetError, Wagers
from ..ui import LayoutView, timestamp
from ._economy import bet_option, log_change, panel

# Resets on every button press, so it covers both answering and picking.
TIMEOUT = 60
NO_MENTIONS = discord.AllowedMentions.none()
_rng = random.SystemRandom()


class RpsView(LayoutView):
    def __init__(self, wagers: Wagers, challenger: discord.Member, opponent: discord.Member, bet: int,
                 hold: int, name: str):
        self._wagers, self._bet, self._name = wagers, bet, name
        self.challenger, self.opponent = challenger, opponent
        self._holds: dict[int, int] = {challenger.id: hold}
        self._picks: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._done = False
        self.message: discord.Message | None = None
        super().__init__(timeout=TIMEOUT)
        self._show_challenge()

    @property
    def _players(self) -> str:
        return f"{self.challenger.mention} vs {self.opponent.mention}"

    def _set(self, text: str, color: discord.Color, *buttons: discord.ui.Button) -> None:
        self.clear_items()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text), color=color))
        if buttons:
            self.add_item(discord.ui.ActionRow(*buttons))

    def _button(self, label: str, style: discord.ButtonStyle, callback) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style)
        button.callback = callback
        return button

    def _show_challenge(self) -> None:
        self._set(
            f"## Rock paper scissors\n{self.opponent.mention}, {self.challenger.mention} challenges you for "
            f"**{self._bet:,}** {self._name} each. Winner takes the pot.",
            discord.Color.blurple(),
            self._button("Accept", discord.ButtonStyle.success, self._accept),
            self._button("Decline", discord.ButtonStyle.secondary, self._decline),
        )

    def _show_picking(self) -> None:
        waiting = [p.mention for p in (self.challenger, self.opponent) if p.id not in self._picks]
        buttons = [self._button(c.capitalize(), discord.ButtonStyle.primary, self._picker(c)) for c in CHOICES]
        self._set(f"## Rock paper scissors\n{self._players}\nWaiting on: {', '.join(waiting)}",
                  discord.Color.blurple(), *buttons)

    async def _refund(self, text: str) -> None:
        self._done = True
        self.stop()
        for hold in self._holds.values():
            await self._wagers.settle(hold, self._bet)
        self._set(f"## Rock paper scissors\n{text}\n-# Bets refunded.", discord.Color.light_grey())

    async def _accept(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return
        async with self._lock:
            if self._done or self.opponent.id in self._holds:
                await interaction.response.defer()
                return
            try:
                hold = await self._wagers.hold(interaction.guild_id, self.opponent.id, self._bet, "rps")
            except PaymentError:
                await interaction.response.send_message(
                    f"You need {self._bet:,} {self._name} to accept.", ephemeral=True)
                return
            self._holds[self.opponent.id] = hold
            self._show_picking()
            await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)

    async def _decline(self, interaction: discord.Interaction) -> None:
        if interaction.user.id not in (self.opponent.id, self.challenger.id):
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return
        async with self._lock:
            if self._done or self.opponent.id in self._holds:
                await interaction.response.defer()
                return
            who = "withdrew the challenge" if interaction.user.id == self.challenger.id else "declined"
            await self._refund(f"{interaction.user.mention} {who}.")
            await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)

    def _picker(self, choice: str):
        async def pick(interaction: discord.Interaction) -> None:
            user_id = interaction.user.id
            if user_id not in (self.challenger.id, self.opponent.id):
                await interaction.response.send_message("You're not in this duel.", ephemeral=True)
                return
            async with self._lock:
                if self._done or user_id in self._picks:
                    await interaction.response.send_message("You've already picked.", ephemeral=True)
                    return
                self._picks[user_id] = choice
                if len(self._picks) < 2:
                    self._show_picking()
                    await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)
                    await interaction.followup.send(f"You picked {choice}.", ephemeral=True)
                    return
                await self._settle()
                await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)
        return pick

    async def _settle(self) -> None:
        self._done = True
        self.stop()
        a, b = self._picks[self.challenger.id], self._picks[self.opponent.id]
        pay_a, pay_b = rps_payouts(a, b, self._bet)
        await self._wagers.settle(self._holds[self.challenger.id], pay_a)
        await self._wagers.settle(self._holds[self.opponent.id], pay_b)
        picks = f"{self.challenger.mention} picked **{a}**, {self.opponent.mention} picked **{b}**."
        if pay_a == pay_b:
            result, color = "It's a tie. Bets refunded.", discord.Color.light_grey()
        else:
            winner = self.challenger if pay_a else self.opponent
            result, color = f"{winner.mention} wins **{self._bet * 2:,}** {self._name}.", discord.Color.green()
        self._set(f"## Rock paper scissors\n{picks}\n{result}", color)

    async def on_timeout(self) -> None:
        async with self._lock:
            if self._done:
                return
            accepted = self.opponent.id in self._holds
            await self._refund("Nobody picked in time." if accepted else f"{self.opponent.mention} didn't answer.")
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                pass


class DuelsCog(DakarCog, name="Duels"):
    @discord.slash_command(name="rps", description="Challenge someone to rock paper scissors for a pot.")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.member)
    async def rps(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Who to challenge"),
        bet: bet_option("How much each of you puts in"),
    ) -> None:
        if member.bot or member == ctx.author:
            await ctx.respond("Challenge another member.", ephemeral=True)
            return
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        wagers = Wagers(self.db)
        try:
            hold = await wagers.hold(ctx.guild.id, ctx.author.id, bet, "rps")
        except (BetError, PaymentError) as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        view = RpsView(wagers, ctx.author, member, bet, hold, name)
        await ctx.respond(view=view, allowed_mentions=discord.AllowedMentions(users=[member]))
        view.message = await ctx.interaction.original_response()

    @discord.slash_command(name="rob", description="Try to steal from someone. Get caught and you pay them.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def rob(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Who to rob"),
    ) -> None:
        if not await self.guild_settings.get_rob_enabled(ctx.guild.id):
            await ctx.respond("Robbing is turned off in this server. Admins can turn it on with /currency rob.",
                              ephemeral=True)
            return
        if member.bot:
            await ctx.respond("You can't rob a bot.", ephemeral=True)
            return
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        try:
            result = await Robberies(self.db).attempt(ctx.guild.id, ctx.author.id, member.id, int(time.time()),
                                                      roll_rob(_rng))
        except RobCooldown as cooldown:
            await ctx.respond(f"{cooldown} You can try again {timestamp(cooldown.retry_at)}.", ephemeral=True)
            return
        except RobError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        if result.success:
            text = f"You robbed {member.mention} and got away with **{result.amount:,}** {name}."
            color, change, target_change = discord.Color.green(), result.amount, -result.amount
        else:
            text = f"You got caught robbing {member.mention} and paid them **{result.amount:,}** {name}."
            color, change, target_change = discord.Color.red(), -result.amount, result.amount
        await ctx.respond(view=panel(f"{text}\n-# Balance: {result.robber_balance:,} {name}", color),
                          allowed_mentions=discord.AllowedMentions(users=[member]))
        title = "Robbery" if result.success else "Failed robbery"
        await log_change(self.guild_settings, ctx.guild, title, ctx.author, ctx.author, change, result.robber_balance)
        await log_change(self.guild_settings, ctx.guild, title, ctx.author, member, target_change,
                         result.target_balance)


def setup(bot: DakarBot) -> None:
    bot.add_cog(DuelsCog(bot))
