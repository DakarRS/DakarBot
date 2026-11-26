"""/coinflip, /slots and /blackjack. Instant games settle in one transaction; blackjack
holds the bet until the hand ends, and a timeout stands for the player."""
import asyncio
import random

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..economy.balances import PaymentError
from ..games.blackjack import Blackjack, Outcome, hand_value, new_deck
from ..games.casino import SIDES, coinflip_multiplier, flip, slots_multiplier, spin
from ..games.wagers import BetError, Wagers
from ..ui import LayoutView
from ._economy import bet_option, panel

BLACKJACK_TIMEOUT = 120
_rng = random.SystemRandom()


def result_line(bet: int, payout: int, name: str) -> tuple[str, discord.Color]:
    if payout > bet:
        return f"You won **{payout - bet:,}** {name}.", discord.Color.green()
    if payout == bet:
        return "You got your bet back.", discord.Color.light_grey()
    if payout:
        return f"You lost **{bet - payout:,}** {name}.", discord.Color.red()
    return f"You lost your **{bet:,}** {name}.", discord.Color.red()


def _hand(cards: list[str]) -> str:
    return f"{' '.join(cards)} ({hand_value(cards)})"


_OUTCOME_TEXT = {
    Outcome.BLACKJACK: "Blackjack!", Outcome.WIN: "You win.", Outcome.PUSH: "Push.", Outcome.LOSE: "Dealer wins.",
}


class BlackjackView(LayoutView):
    def __init__(self, wagers: Wagers, game: Blackjack, bet: int, hold_id: int, user_id: int, name: str):
        self._wagers, self.game, self._bet, self._hold = wagers, game, bet, hold_id
        self._user_id, self._name = user_id, name
        self._lock = asyncio.Lock()
        self.message: discord.Message | None = None
        super().__init__(timeout=BLACKJACK_TIMEOUT)
        self._render()

    def _render(self, footer: str = "") -> None:
        game = self.game
        dealer = _hand(game.dealer) if game.finished else f"{game.dealer[0]} ?"
        lines = [f"## Blackjack\n**Your hand:** {_hand(game.player)}\n**Dealer:** {dealer}"]
        color = discord.Color.blurple()
        if footer:
            lines.append(footer)
            color = discord.Color.green() if game.outcome in (Outcome.WIN, Outcome.BLACKJACK) else (
                discord.Color.light_grey() if game.outcome is Outcome.PUSH else discord.Color.red())
        self.clear_items()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay("\n".join(lines)), color=color))
        if not game.finished:
            buttons = []
            for label, style, handler, enabled in (
                ("Hit", discord.ButtonStyle.primary, self._hit, True),
                ("Stand", discord.ButtonStyle.secondary, self._stand, True),
                ("Double", discord.ButtonStyle.success, self._double, game.can_double),
            ):
                button = discord.ui.Button(label=label, style=style, disabled=not enabled)
                button.callback = handler
                buttons.append(button)
            self.add_item(discord.ui.ActionRow(*buttons))

    async def finish(self) -> None:
        """Settles the hold and redraws the final hand. Safe to call more than once."""
        bet = self._bet * 2 if self.game.doubled else self._bet
        payout = self.game.payout(self._bet)
        balance = await self._wagers.settle(self._hold, payout)
        self.stop()
        footer = f"**{_OUTCOME_TEXT[self.game.outcome]}** {result_line(bet, payout, self._name)[0]}"
        if balance is not None:
            footer += f"\n-# Balance: {balance:,} {self._name}"
        self._render(footer)

    async def _move(self, interaction: discord.Interaction, move) -> None:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Start your own game with /blackjack.", ephemeral=True)
            return
        async with self._lock:
            if self.game.finished:
                await interaction.response.defer()
                return
            if not await move(interaction):
                return
            if self.game.finished:
                await self.finish()
            else:
                self._render()
            await interaction.response.edit_message(view=self)

    async def _hit(self, interaction: discord.Interaction) -> None:
        async def move(_):
            self.game.hit()
            return True
        await self._move(interaction, move)

    async def _stand(self, interaction: discord.Interaction) -> None:
        async def move(_):
            self.game.stand()
            return True
        await self._move(interaction, move)

    async def _double(self, interaction: discord.Interaction) -> None:
        async def move(i: discord.Interaction):
            try:
                if await self._wagers.raise_hold(self._hold, self._bet) is None:
                    await i.response.defer()
                    return False
            except PaymentError:
                await i.response.send_message("You don't have enough to double.", ephemeral=True)
                return False
            self.game.double()
            return True
        await self._move(interaction, move)

    async def on_timeout(self) -> None:
        async with self._lock:
            if self.game.finished:
                return
            self.game.stand()
            await self.finish()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class GamblingCog(DakarCog, name="Gambling"):
    @property
    def wagers(self) -> Wagers:
        return Wagers(self.db)

    async def _play(self, ctx: discord.ApplicationContext, bet: int, multiplier, heading: str) -> None:
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        try:
            played = await self.wagers.play(ctx.guild.id, ctx.author.id, bet, multiplier)
        except (BetError, PaymentError) as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        text, color = result_line(bet, played.payout, name)
        await ctx.respond(view=panel(f"{heading}\n{text}\n-# Balance: {played.balance:,} {name}", color))

    @discord.slash_command(name="coinflip", description="Call heads or tails. A correct call doubles your bet.")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.member)
    async def coinflip(
        self,
        ctx: discord.ApplicationContext,
        bet: bet_option(),
        call: discord.Option(str, description="Your call", choices=list(SIDES)),
    ) -> None:
        result = flip(_rng)
        await self._play(ctx, bet, coinflip_multiplier(call, result), f"## Coinflip\nIt landed on **{result}**.")

    @discord.slash_command(name="slots", description="Spin the slot machine.")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.member)
    async def slots(self, ctx: discord.ApplicationContext, bet: bet_option()) -> None:
        reels = spin(_rng)
        shown = " | ".join(reels)
        await self._play(ctx, bet, slots_multiplier(reels), f"## Slots\n`{shown}`")

    @discord.slash_command(name="blackjack", description="Play a hand of blackjack against the dealer.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def blackjack(self, ctx: discord.ApplicationContext, bet: bet_option()) -> None:
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        try:
            hold = await self.wagers.hold(ctx.guild.id, ctx.author.id, bet, "blackjack")
        except (BetError, PaymentError) as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        view = BlackjackView(self.wagers, Blackjack(new_deck(_rng)), bet, hold, ctx.author.id, name)
        if view.game.finished:
            await view.finish()
        await ctx.respond(view=view)
        if not view.game.finished:
            view.message = await ctx.interaction.original_response()


def setup(bot: DakarBot) -> None:
    bot.add_cog(GamblingCog(bot))
