"""Per-server currency, ported from exorium: /balance, /balance-reset, /work, /daily, /pay and
the admin /currency group. Discord won't let /balance be both a command and a group,
which is why reset is /balance-reset rather than /balance reset."""
import random
from typing import Optional

import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..economy.balances import (
    MAX_BALANCE,
    WORK_COOLDOWN_SECONDS,
    Balances,
    PaymentError,
    WorkResult,
    roll_work,
    validate_payment,
)
from ..economy.daily import DAILY_MAX_STREAK, AlreadyClaimed, Daily, daily_reward, next_claim_at, utc_today
from ..ui import ConfirmView
from ._economy import log_change, panel

MANAGE_GUILD = discord.Permissions(manage_guild=True)
_rng = random.SystemRandom()


class CurrencyCog(DakarCog, name="Currency"):
    @property
    def balances(self) -> Balances:
        return Balances(self.db)

    async def _name(self, guild_id: int) -> str:
        return await self.guild_settings.get_currency_name(guild_id)

    async def log_change(self, guild: discord.Guild, title: str, actor: discord.abc.User,
                         target: discord.abc.User, change: Optional[int], new_balance: int) -> None:
        await log_change(self.guild_settings, guild, title, actor, target, change, new_balance)

    @discord.slash_command(name="balance", description="Show your balance, or someone else's.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def balance(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Whose balance to show", required=False, default=None),
    ) -> None:
        member = member or ctx.author
        amount = await self.balances.get(ctx.guild.id, member.id)
        name = await self._name(ctx.guild.id)
        heading = "Your balance" if member == ctx.author else f"{member.display_name}'s balance"
        text = f"## {discord.utils.escape_markdown(heading)}\n**{amount:,}** {name}"
        await ctx.respond(view=panel(text, discord.Color.green()))

    @discord.slash_command(name="balance-reset", description="Reset your balance in this server to 0.")
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.member)
    async def balance_reset(self, ctx: discord.ApplicationContext) -> None:
        guild, author = ctx.guild, ctx.author

        async def reset() -> None:
            await self.balances.reset(guild.id, author.id)
            await ctx.followup.send("Your balance in this server has been reset to 0.", ephemeral=True)
            await self.log_change(guild, "Balance reset", author, author, None, 0)

        container = discord.ui.Container(
            discord.ui.TextDisplay("## Reset your balance?"),
            discord.ui.TextDisplay("This sets your balance in this server to 0 and can't be undone."),
            color=discord.Color.red(),
        )
        view = ConfirmView(container, reset, danger=True, user_id=author.id)
        await ctx.respond(view=view, ephemeral=True)
        view.message = await ctx.interaction.original_response()

    @discord.slash_command(name="work", description="Work for some money.")
    @commands.guild_only()
    @commands.cooldown(1, WORK_COOLDOWN_SECONDS, commands.BucketType.member)
    async def work(self, ctx: discord.ApplicationContext) -> None:
        outcome = roll_work(_rng)
        change, new_balance = await self.balances.apply_work(ctx.guild.id, ctx.author.id, outcome)
        name = await self._name(ctx.guild.id)

        if outcome.result is WorkResult.EARNED:
            text, color = f"You worked hard and earned **{change:,}** {name}.", discord.Color.green()
        elif outcome.result is WorkResult.NOTHING:
            text, color = "You showed up late and didn't earn anything today.", discord.Color.light_grey()
        elif change:
            text, color = f"You broke a machine and your boss charged you **{-change:,}** {name}.", discord.Color.red()
        else:
            text, color = "You broke a machine, but you had nothing for your boss to charge.", discord.Color.red()
        text += f"\n-# Balance: {new_balance:,} {name}"
        await ctx.respond(view=panel(text, color))
        if change:
            await self.log_change(ctx.guild, "Work", self.bot.user, ctx.author, change, new_balance)

    @discord.slash_command(name="daily", description="Claim your daily reward. Come back every day for a streak.")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.member)
    async def daily(self, ctx: discord.ApplicationContext) -> None:
        today = utc_today()
        name = await self._name(ctx.guild.id)
        try:
            claim = await Daily(self.db).claim(ctx.guild.id, ctx.author.id, today)
        except AlreadyClaimed:
            next_at = discord.utils.format_dt(next_claim_at(today), "R")
            await ctx.respond(f"You've already claimed today. Your next daily is ready {next_at}.", ephemeral=True)
            return
        lines = [f"## Daily reward\nYou got **{claim.reward:,}** {name}."]
        if claim.streak > 1:
            lines.append(f"**Streak:** {claim.streak} days")
        if claim.streak < DAILY_MAX_STREAK:
            lines.append(f"-# Come back tomorrow for {daily_reward(claim.streak + 1):,}.")
        lines.append(f"-# Balance: {claim.balance:,} {name}")
        await ctx.respond(view=panel("\n".join(lines), discord.Color.green()))
        if claim.reward:
            await self.log_change(ctx.guild, "Daily", self.bot.user, ctx.author, claim.reward, claim.balance)

    @discord.slash_command(name="pay", description="Pay another member from your balance.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pay(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Who to pay"),
        amount: discord.Option(int, description="How much", min_value=1, max_value=MAX_BALANCE),
    ) -> None:
        name = await self._name(ctx.guild.id)
        try:
            validate_payment(ctx.author.id, member.id, member.bot, amount)
            payer_balance, payee_balance = await self.balances.transfer(ctx.guild.id, ctx.author.id, member.id, amount)
        except PaymentError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        text = f"You paid {member.mention} **{amount:,}** {name}.\n-# Your balance: {payer_balance:,} {name}"
        await ctx.respond(view=panel(text, discord.Color.green()))
        await self.log_change(ctx.guild, "Payment", ctx.author, member, amount, payee_balance)

    currency = discord.SlashCommandGroup(
        "currency", "Manage this server's currency.", default_member_permissions=MANAGE_GUILD, guild_only=True
    )

    @currency.command(name="set", description="Set a member's balance.")
    async def currency_set(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Whose balance to set"),
        amount: discord.Option(int, description="The new balance", min_value=0, max_value=MAX_BALANCE),
    ) -> None:
        if member.bot:
            await ctx.respond("Bots don't have balances.", ephemeral=True)
            return
        await self.balances.set(ctx.guild.id, member.id, amount)
        name = await self._name(ctx.guild.id)
        await ctx.respond(f"Set {member.mention}'s balance to **{amount:,}** {name}.",
                          allowed_mentions=discord.AllowedMentions.none())
        await self.log_change(ctx.guild, "Balance set", ctx.author, member, None, amount)

    @currency.command(name="name", description="Rename this server's currency.")
    async def currency_name(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, description="Leave empty for the default", required=False, default=None,
                             max_length=32),
    ) -> None:
        cleaned = name.strip() if name else None
        await self.guild_settings.set_currency_name(ctx.guild.id, cleaned or None)
        await ctx.respond(f"The currency is now called **{await self._name(ctx.guild.id)}**.")


def setup(bot: DakarBot) -> None:
    bot.add_cog(CurrencyCog(bot))
