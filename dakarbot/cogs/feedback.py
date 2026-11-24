"""/suggest: bot-wide suggestions posted to one channel, with owner-only Approve/Deny
buttons that ask for a reason, recolor the post and DM the submitter. Ports exorium's
suggest and suggestion approve/deny.

The buttons are matched by custom_id in on_interaction instead of a registered view,
so they keep working on posts made before a restart."""
import logging
from typing import Optional

import discord
from discord.ext import commands

from .. import settings
from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..suggestions import (
    Suggestion,
    Suggestions,
    SuggestionStatus,
    decision_custom_id,
    parse_decision_custom_id,
)
from ..ui import LayoutView, report_interaction_error

logger = logging.getLogger("dakarbot.feedback")

_STATUS_COLORS = {
    SuggestionStatus.PENDING: discord.Color.orange(),
    SuggestionStatus.APPROVED: discord.Color.green(),
    SuggestionStatus.DENIED: discord.Color.red(),
}


def build_suggestion_view(suggestion: Suggestion, guild_name: Optional[str]) -> LayoutView:
    origin = f" in {discord.utils.escape_markdown(guild_name)}" if guild_name else ""
    items: list[discord.ui.Item] = [
        discord.ui.TextDisplay(f"## Suggestion #{suggestion.id}"),
        discord.ui.TextDisplay(suggestion.content),
        discord.ui.TextDisplay(f"-# From <@{suggestion.user_id}> (`{suggestion.user_id}`){origin}"),
    ]
    if suggestion.status is not SuggestionStatus.PENDING:
        verdict = f"**{suggestion.status.value.title()}** by <@{suggestion.decided_by}>"
        if suggestion.decision_reason:
            verdict += f"\n{suggestion.decision_reason}"
        items += [discord.ui.Separator(), discord.ui.TextDisplay(verdict)]

    container = discord.ui.Container(*items, color=_STATUS_COLORS[suggestion.status])
    if suggestion.status is not SuggestionStatus.PENDING:
        return LayoutView(container)
    buttons = discord.ui.ActionRow(
        discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success,
            custom_id=decision_custom_id(SuggestionStatus.APPROVED, suggestion.id),
        ),
        discord.ui.Button(
            label="Deny", style=discord.ButtonStyle.danger,
            custom_id=decision_custom_id(SuggestionStatus.DENIED, suggestion.id),
        ),
    )
    return LayoutView(container, buttons, timeout=None)


class DecisionModal(discord.ui.DesignerModal):
    def __init__(self, cog: "FeedbackCog", status: SuggestionStatus, suggestion_id: int):
        self.cog = cog
        self.status = status
        self.suggestion_id = suggestion_id
        self.reason = discord.ui.InputText(style=discord.InputTextStyle.long, required=False, max_length=1000)
        verb = "Approve" if status is SuggestionStatus.APPROVED else "Deny"
        super().__init__(
            discord.ui.Label(label="Reason", description="Optional; sent to the submitter.", item=self.reason),
            title=f"{verb} suggestion #{suggestion_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        decided = await self.cog.store.decide(
            self.suggestion_id, self.status, interaction.user.id, self.reason.value or None
        )
        if decided is None:
            await interaction.response.send_message("That suggestion was already decided.", ephemeral=True)
            return
        guild = self.cog.bot.get_guild(decided.guild_id) if decided.guild_id else None
        await interaction.response.edit_message(view=build_suggestion_view(decided, guild.name if guild else None))
        await self.cog.notify_submitter(decided)

    async def on_error(self, error: Exception, interaction: discord.Interaction) -> None:
        await report_interaction_error(f"Suggestion #{self.suggestion_id} decision", error, interaction)


class FeedbackCog(DakarCog, name="Feedback"):
    @property
    def store(self) -> Suggestions:
        return Suggestions(self.db)

    @discord.slash_command(name="suggest", description="Suggest something for the bot.", **EVERYWHERE)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def suggest(
        self,
        ctx: discord.ApplicationContext,
        suggestion: discord.Option(str, description="Your suggestion", max_length=1000),
    ) -> None:
        channel_id = settings.SUGGESTIONS_CHANNEL_ID
        if channel_id is None:
            await ctx.respond("Suggestions aren't set up on this bot.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        record = await self.store.create(ctx.author.id, ctx.guild_id, suggestion)
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        view = build_suggestion_view(record, getattr(ctx.guild, "name", None))
        message = await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        view.stop()  # clicks go through on_interaction, not this in-memory view
        await self.store.set_message(record.id, message.channel.id, message.id)
        await ctx.respond("Thanks, your suggestion was sent to the developers.", ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        parsed = parse_decision_custom_id((interaction.data or {}).get("custom_id", ""))
        if parsed is None:
            return
        status, suggestion_id = parsed
        try:
            if not await self.bot.is_owner(interaction.user):
                await interaction.response.send_message(
                    "Only the bot owner can decide on suggestions.", ephemeral=True
                )
                return
            await interaction.response.send_modal(DecisionModal(self, status, suggestion_id))
        except Exception as error:
            await report_interaction_error(f"Suggestion #{suggestion_id} button", error, interaction)

    async def notify_submitter(self, suggestion: Suggestion) -> None:
        try:
            user = await self.bot.get_or_fetch_user(suggestion.user_id)
            if user is None:
                return
            lines = [f"## Your suggestion was {suggestion.status.value}", suggestion.content]
            if suggestion.decision_reason:
                lines.append(f"**Reason:** {suggestion.decision_reason}")
            container = discord.ui.Container(
                *(discord.ui.TextDisplay(line) for line in lines), color=_STATUS_COLORS[suggestion.status]
            )
            await user.send(view=LayoutView(container))
        except discord.HTTPException:
            pass  # DMs closed; the decision still shows on the post


def setup(bot: DakarBot) -> None:
    bot.add_cog(FeedbackCog(bot))
