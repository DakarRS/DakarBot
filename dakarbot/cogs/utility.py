"""/emoji, /poll and /announce, ported from exorium's help cog. /poll uses Discord's
native polls instead of reaction voting, and /announce replaces both exorium's
announce and say commands."""
import discord
from discord.ext import commands

from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..polls import DURATION_CHOICES_HOURS, QUESTION_MAX_LENGTH, PollInputError, parse_answers
from ..ui import LayoutView, report_interaction_error

MANAGE_MESSAGES = discord.Permissions(manage_messages=True)

ANNOUNCE_COLORS = {
    "Blurple": discord.Color.blurple(),
    "Green": discord.Color.green(),
    "Red": discord.Color.red(),
    "Orange": discord.Color.orange(),
    "Gold": discord.Color.gold(),
    "Grey": discord.Color.light_grey(),
}


class _Form(discord.ui.DesignerModal):
    async def on_error(self, error: Exception, interaction: discord.Interaction) -> None:
        await report_interaction_error(f"{type(self).__name__} form", error, interaction)


class PollModal(_Form):
    def __init__(self, duration_hours: int, multiple: bool):
        self.duration_hours = duration_hours
        self.multiple = multiple
        self.question = discord.ui.InputText(max_length=QUESTION_MAX_LENGTH)
        self.answers = discord.ui.InputText(style=discord.InputTextStyle.long, max_length=700)
        super().__init__(
            discord.ui.Label(label="Question", item=self.question),
            discord.ui.Label(label="Answers", description="One per line, 2 to 10.", item=self.answers),
            title="Create a poll",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            answers = parse_answers(self.answers.value or "")
        except PollInputError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        poll = discord.Poll(
            question=self.question.value,
            answers=[discord.PollAnswer(text=a) for a in answers],
            duration=self.duration_hours,
            allow_multiselect=self.multiple,
        )
        await interaction.response.send_message(poll=poll)


class AnnounceModal(_Form):
    def __init__(self, channel: discord.TextChannel, color: discord.Color):
        self.channel = channel
        self.color = color
        self.heading = discord.ui.InputText(required=False, max_length=200)
        self.body = discord.ui.InputText(style=discord.InputTextStyle.long, max_length=3800)
        super().__init__(
            discord.ui.Label(label="Title", description="Optional.", item=self.heading),
            discord.ui.Label(label="Message", item=self.body),
            title=f"Announce in #{channel.name}"[:45],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        items: list[discord.ui.Item] = []
        if self.heading.value:
            items.append(discord.ui.TextDisplay(f"## {self.heading.value}"))
        items.append(discord.ui.TextDisplay(self.body.value))
        items.append(discord.ui.TextDisplay(f"-# Posted by {interaction.user.mention}"))
        try:
            message = await self.channel.send(
                view=LayoutView(discord.ui.Container(*items, color=self.color)),
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I can't post in {self.channel.mention}. Check my permissions there.", ephemeral=True
            )
            return
        await interaction.response.send_message(f"Announcement posted: {message.jump_url}", ephemeral=True)


def _emoji_info_view(bot: DakarBot, emoji: discord.PartialEmoji) -> LayoutView:
    created = int(discord.utils.snowflake_time(emoji.id).timestamp())
    lines = [
        f"**Name:** {emoji.name}",
        f"**ID:** {emoji.id}",
        f"**Animated:** {'Yes' if emoji.animated else 'No'}",
        f"**Created:** <t:{created}:D>",
    ]
    known = bot.get_emoji(emoji.id)
    if known is not None and known.guild is not None:
        lines.append(f"**Server:** {discord.utils.escape_markdown(known.guild.name)}")
    container = discord.ui.Container(
        discord.ui.TextDisplay(f"## :{emoji.name}:"),
        discord.ui.MediaGallery(discord.MediaGalleryItem(emoji.url)),
        discord.ui.TextDisplay("\n".join(lines)),
        color=discord.Color.blurple(),
    )
    download = discord.ui.ActionRow(discord.ui.Button(label="Download", url=emoji.url))
    return LayoutView(container, download)


class UtilityCog(DakarCog, name="Utility"):
    @discord.slash_command(name="emoji", description="Show a custom emoji's image and details.", **EVERYWHERE)
    async def emoji(
        self,
        ctx: discord.ApplicationContext,
        emoji: discord.Option(str, description="A custom emoji, e.g. :blobwave:"),
    ) -> None:
        parsed = discord.PartialEmoji.from_str(emoji.strip())
        if parsed.id is None:
            await ctx.respond("That isn't a custom emoji. Pick one from the emoji menu.", ephemeral=True)
            return
        await ctx.respond(view=_emoji_info_view(self.bot, parsed))

    @discord.slash_command(name="poll", description="Start a poll in this channel.", **EVERYWHERE)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def poll(
        self,
        ctx: discord.ApplicationContext,
        duration: discord.Option(
            str, description="How long voting stays open", choices=list(DURATION_CHOICES_HOURS), default="1 day"
        ),
        multiple: discord.Option(bool, description="Allow picking more than one answer", default=False),
    ) -> None:
        await ctx.send_modal(PollModal(DURATION_CHOICES_HOURS[duration], multiple))

    @discord.slash_command(
        name="announce", description="Post an announcement in a channel.",
        default_member_permissions=MANAGE_MESSAGES,
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def announce(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(discord.TextChannel, description="Where to post it"),
        color: discord.Option(str, description="Accent color", choices=list(ANNOUNCE_COLORS), default="Blurple"),
    ) -> None:
        if not channel.permissions_for(ctx.author).send_messages:
            await ctx.respond(f"You can't post in {channel.mention} yourself.", ephemeral=True)
            return
        await ctx.send_modal(AnnounceModal(channel, ANNOUNCE_COLORS[color]))


def setup(bot: DakarBot) -> None:
    bot.add_cog(UtilityCog(bot))
