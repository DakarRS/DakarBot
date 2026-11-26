"""/remind in|list|cancel, and the loop that delivers them. Reminders go by DM, and fall
back to the channel they were set in if the DM fails and the bot can post there."""
import logging
import time

import discord
from discord.ext import tasks

from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..error_reporting import restart_crashed_loop
from ..moderation.duration import DurationTooLong, format_duration, parse_duration
from ..reminders import MAX_TEXT_LENGTH, Reminder, ReminderError, Reminders
from ..ui import LayoutView, timestamp

logger = logging.getLogger("dakarbot.reminders")

CHECK_INTERVAL_SECONDS = 30
LIST_PREVIEW = 80


def _panel(text: str) -> LayoutView:
    return LayoutView(discord.ui.Container(discord.ui.TextDisplay(text), color=discord.Color.teal()))


async def _own_reminders(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    reminders = await Reminders(ctx.cog.db).list(ctx.interaction.user.id)
    return [
        discord.OptionChoice(f"#{r.id}: {r.text[:60]}", r.id)
        for r in reminders if (ctx.value or "") in f"#{r.id} {r.text}"
    ][:25]


class RemindersCog(DakarCog, name="Reminders"):
    def __init__(self, bot: DakarBot):
        super().__init__(bot)
        self.deliver_due.start()

    def cog_unload(self) -> None:
        self.deliver_due.cancel()

    @property
    def reminders(self) -> Reminders:
        return Reminders(self.db)

    remind = discord.SlashCommandGroup("remind", "Set reminders for yourself.", **EVERYWHERE)

    @remind.command(name="in", description="Remind you about something after a while.")
    async def remind_in(
        self,
        ctx: discord.ApplicationContext,
        when: discord.Option(str, description="How long from now, like 10m, 2h or 1d 12h", max_length=50),
        text: discord.Option(str, description="What to remind you about", max_length=MAX_TEXT_LENGTH),
    ) -> None:
        try:
            seconds = parse_duration(when)
        except DurationTooLong:
            await ctx.respond("Reminders can be at most 10 years away.", ephemeral=True)
            return
        if seconds is None:
            await ctx.respond("I didn't understand that time. Try something like 10m, 2h or 1d 12h.", ephemeral=True)
            return
        # Only remember the channel if the bot can post there, for the DM fallback.
        channel_id = ctx.channel_id if ctx.guild_id and self.bot.get_guild(ctx.guild_id) else None
        now = int(time.time())
        try:
            reminder = await self.reminders.add(ctx.author.id, channel_id, text, now, seconds)
        except ReminderError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        due = timestamp(reminder.due)
        await ctx.respond(f"I'll remind you {due} ({format_duration(seconds)}). It's reminder #{reminder.id}.",
                          ephemeral=True)

    @remind.command(name="list", description="List your reminders.")
    async def remind_list(self, ctx: discord.ApplicationContext) -> None:
        reminders = await self.reminders.list(ctx.author.id)
        if not reminders:
            await ctx.respond("You have no reminders. Set one with /remind in.", ephemeral=True)
            return
        lines = [
            f"**#{r.id}** {timestamp(r.due)}: "
            f"{discord.utils.escape_markdown(r.text[:LIST_PREVIEW])}"
            for r in reminders
        ]
        await ctx.respond(view=_panel("## Your reminders\n" + "\n".join(lines)), ephemeral=True)

    @remind.command(name="cancel", description="Cancel one of your reminders.")
    async def remind_cancel(
        self,
        ctx: discord.ApplicationContext,
        reminder: discord.Option(int, description="Which reminder", autocomplete=_own_reminders, min_value=1),
    ) -> None:
        cancelled = await self.reminders.cancel(ctx.author.id, reminder)
        await ctx.respond(f"Cancelled reminder #{reminder}." if cancelled else f"You have no reminder #{reminder}.",
                          ephemeral=True)

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def deliver_due(self) -> None:
        for reminder in await self.reminders.take_due(int(time.time())):
            await self._deliver(reminder)

    @deliver_due.error
    async def deliver_due_error(self, error: BaseException) -> None:
        restart_crashed_loop(self.deliver_due, error)

    @deliver_due.before_loop
    async def before_deliver_due(self) -> None:
        await self.bot.wait_until_ready()

    async def _deliver(self, reminder: Reminder) -> None:
        set_at = timestamp(reminder.created)
        body = f"## Reminder\n{discord.utils.escape_markdown(reminder.text)}\n-# You set this {set_at}."
        try:
            user = self.bot.get_user(reminder.user_id) or await self.bot.fetch_user(reminder.user_id)
            await user.send(view=_panel(body))
            return
        except discord.HTTPException:
            pass
        channel = self.bot.get_channel(reminder.channel_id) if reminder.channel_id else None
        if channel is None:
            logger.info("Couldn't deliver reminder #%s to %s", reminder.id, reminder.user_id)
            return
        try:
            # Components V2 messages can't have plain content, so the ping goes in the panel.
            await channel.send(view=_panel(f"<@{reminder.user_id}>\n{body}"),
                               allowed_mentions=discord.AllowedMentions(users=[discord.Object(reminder.user_id)]))
        except discord.HTTPException:
            logger.info("Couldn't deliver reminder #%s to %s", reminder.id, reminder.user_id)


def setup(bot: DakarBot) -> None:
    bot.add_cog(RemindersCog(bot))
