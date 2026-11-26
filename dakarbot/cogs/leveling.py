"""XP from chatting: level-up announcements, payouts and role rewards, /rank, /cash-in
and the admin /levels-admin group."""
import logging
import random
import time

import discord
from discord.ext import commands

from ..blacklist import BlacklistKind
from ..bot import DakarBot, DakarCog
from ..economy.leaderboard import levels as level_ranking
from ..leveling.curve import progress, roll_xp
from ..leveling.levels import MAX_REWARD_LEVEL, XP_PER_COIN, Award, LevelError, Levels
from ..ui import ConfirmView
from ._economy import log_change, panel, role_problem

logger = logging.getLogger("dakarbot.leveling")

MANAGE_GUILD = discord.Permissions(manage_guild=True)
NO_MENTIONS = discord.AllowedMentions.none()
BAR_WIDTH = 12
_rng = random.SystemRandom()


def progress_bar(done: int, total: int, width: int = BAR_WIDTH) -> str:
    filled = min(width, done * width // total) if total else width
    return "█" * filled + "░" * (width - filled)


class LevelingCog(DakarCog, name="Leveling"):
    @property
    def levels(self) -> Levels:
        return Levels(self.db)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or self.bot.db is None or self.bot.blacklist is None:
            return
        if self.blacklist.is_blacklisted(BlacklistKind.USER, message.author.id) or self.blacklist.is_blacklisted(
            BlacklistKind.SERVER, message.guild.id
        ):
            return
        award = await self.levels.award(message.guild.id, message.author.id, roll_xp(_rng), int(time.time()))
        if award is None or not award.new_levels:
            return
        granted = await self._grant_rewards(message.author, award)
        await self._announce(message, award, granted)
        if award.payout:
            await log_change(self.guild_settings, message.guild, "Level up", self.bot.user, message.author,
                             award.payout, award.balance, detail=f"**Level:** {award.level}")

    async def _grant_rewards(self, member: discord.Member, award: Award) -> list[discord.Role]:
        roles = [r for r in (member.guild.get_role(i) for i in award.role_ids) if r and r not in member.roles]
        if not roles:
            return []
        try:
            await member.add_roles(*roles, reason=f"Reached level {award.level}")
        except discord.HTTPException:
            logger.warning("Couldn't give level rewards to %s in guild %s", member.id, member.guild.id)
            return []
        return roles

    async def _announce(self, message: discord.Message, award: Award, roles: list[discord.Role]) -> None:
        target = await self.guild_settings.get_level_up_target(message.guild.id)
        if target == "off":
            return
        channel = message.channel if target is None else message.guild.get_channel(target)
        if channel is None:
            return
        name = await self.guild_settings.get_currency_name(message.guild.id)
        text = f"{message.author.mention} reached **level {award.level}**"
        text += f" and earned **{award.payout:,}** {name}." if award.payout else "."
        if roles:
            text += "\nNew role" + ("s: " if len(roles) > 1 else ": ") + ", ".join(r.mention for r in roles)
        try:
            await channel.send(view=panel(text, discord.Color.purple()), allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            logger.warning("Couldn't announce a level-up in channel %s", channel.id)

    @discord.slash_command(name="rank", description="Show your level, or someone else's.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rank(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Whose level to show", required=False, default=None),
    ) -> None:
        member = member or ctx.author
        info = await self.levels.get(ctx.guild.id, member.id)
        level, into, needed = progress(info.xp)
        ranking = await level_ranking(self.db, ctx.guild.id, member.id, limit=1)
        whose = "Your" if member == ctx.author else f"{member.display_name}'s"
        lines = [
            f"## {discord.utils.escape_markdown(whose)} level",
            f"**Level {level}**" + (f" - #{ranking.caller.rank} in this server" if ranking.caller else ""),
            f"`{progress_bar(into, needed)}` {into:,} / {needed:,} XP",
            f"-# Total: {info.xp:,} XP",
        ]
        await ctx.respond(view=panel("\n".join(lines), discord.Color.purple()))

    @discord.slash_command(name="cash-in", description="Trade XP for currency. This lowers your level.")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.member)
    async def cash_in(
        self,
        ctx: discord.ApplicationContext,
        xp: discord.Option(int, description="How much XP to trade (all of it if empty)", min_value=1,
                           required=False, default=None),
    ) -> None:
        guild, author = ctx.guild, ctx.author
        info = await self.levels.get(guild.id, author.id)
        amount = xp or info.xp
        if amount <= 0 or amount > info.xp:
            await ctx.respond(f"You have {info.xp:,} XP to cash in.", ephemeral=True)
            return
        name = await self.guild_settings.get_currency_name(guild.id)
        after = progress(info.xp - amount)[0]

        async def confirm() -> None:
            try:
                result = await self.levels.cash_in(guild.id, author.id, amount)
            except LevelError as error:
                await ctx.followup.send(str(error), ephemeral=True)
                return
            await ctx.followup.send(
                f"You traded {result.coins * XP_PER_COIN:,} XP for **{result.coins:,}** {name}. "
                f"You're now level {result.level}.\n-# Balance: {result.balance:,} {name}",
                ephemeral=True,
            )
            await log_change(self.guild_settings, guild, "Cash-in", author, author, result.coins, result.balance)

        container = discord.ui.Container(
            discord.ui.TextDisplay("## Cash in XP?"),
            discord.ui.TextDisplay(
                f"Trade **{amount:,} XP** for **{amount // XP_PER_COIN:,}** {name}. "
                f"You'll go from level {info.level} to level {after}.\n"
                "-# Level-up rewards you've already had won't pay out again."
            ),
            color=discord.Color.purple(),
        )
        view = ConfirmView(container, confirm, user_id=author.id)
        await ctx.respond(view=view, ephemeral=True)
        view.message = await ctx.interaction.original_response()

    levels_admin = discord.SlashCommandGroup(
        "levels-admin", "Manage leveling in this server.", default_member_permissions=MANAGE_GUILD, guild_only=True
    )

    @levels_admin.command(name="announce", description="Choose where level-ups are announced.")
    async def announce(
        self,
        ctx: discord.ApplicationContext,
        where: discord.Option(str, description="Where to post them", choices=["Where they chatted", "A channel", "Off"]),
        channel: discord.Option(discord.TextChannel, description="For \"A channel\"", required=False, default=None),
    ) -> None:
        if where == "A channel":
            if channel is None:
                await ctx.respond("Pick the channel to post level-ups in.", ephemeral=True)
                return
            await self.guild_settings.set_level_up_target(ctx.guild.id, channel.id)
            await ctx.respond(f"Level-ups will be announced in {channel.mention}.")
            return
        await self.guild_settings.set_level_up_target(ctx.guild.id, "off" if where == "Off" else None)
        await ctx.respond("Level-ups won't be announced." if where == "Off"
                          else "Level-ups will be announced where the member chatted.")

    @levels_admin.command(name="reward-set", description="Give a role when members reach a level.")
    async def reward_set(
        self,
        ctx: discord.ApplicationContext,
        level: discord.Option(int, description="The level", min_value=1, max_value=MAX_REWARD_LEVEL),
        role: discord.Option(discord.Role, description="The role to give"),
    ) -> None:
        if problem := role_problem(role, ctx.author):
            await ctx.respond(problem, ephemeral=True)
            return
        try:
            await self.levels.set_reward(ctx.guild.id, level, role.id)
        except LevelError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        await ctx.respond(f"Members who reach level {level} from now on get {role.mention}.",
                          allowed_mentions=NO_MENTIONS)

    @levels_admin.command(name="reward-remove", description="Stop giving a role at a level.")
    async def reward_remove(
        self,
        ctx: discord.ApplicationContext,
        level: discord.Option(int, description="The level", min_value=1, max_value=MAX_REWARD_LEVEL),
    ) -> None:
        removed = await self.levels.remove_reward(ctx.guild.id, level)
        await ctx.respond(f"Removed the level {level} reward." if removed else f"Level {level} has no reward.",
                          ephemeral=not removed)

    @levels_admin.command(name="rewards", description="List the level role rewards.")
    async def rewards(self, ctx: discord.ApplicationContext) -> None:
        rewards = await self.levels.rewards(ctx.guild.id)
        body = "\n".join(f"**Level {level}** - <@&{role_id}>" for level, role_id in rewards) or "None yet."
        await ctx.respond(view=panel(f"## Level rewards\n{body}", discord.Color.purple()),
                          allowed_mentions=NO_MENTIONS, ephemeral=True)


def setup(bot: DakarBot) -> None:
    bot.add_cog(LevelingCog(bot))
