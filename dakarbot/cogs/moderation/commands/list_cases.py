"""Implementation for /moderation list, including its paginated case-list view (used
only here, so it lives alongside the command rather than in a shared views module)."""
from typing import TYPE_CHECKING

import discord

from dakarbot.moderation import models
from dakarbot.moderation.models import MODERATIONS_PER_PAGE, Moderation, ModerationTarget, describe_case_line
from dakarbot.ui import Icon, LayoutView

if TYPE_CHECKING:
    from ..base import ModerationCog


async def run(cog: "ModerationCog", ctx: discord.ApplicationContext, user: discord.User) -> None:
    records = await models.list_for_user(cog.db, ctx.guild.id, user.id)
    view = ModerationListView(records, user)
    await ctx.respond(view=view, ephemeral=True)


class ModerationListView(LayoutView):
    """BaseView doesn't collect @discord.ui.button-decorated methods the way the
    legacy View does (that collection machinery only exists on View), so buttons
    are built and wired by hand in __init__ instead."""

    def __init__(self, moderations: list[Moderation], target: ModerationTarget):
        self.moderations = moderations
        self.target = target
        self.page = 0

        self.header = discord.ui.TextDisplay(self._header_text())
        self.body = discord.ui.TextDisplay(self._body_text())
        self.first_button = discord.ui.Button(label="First", emoji="⏮️", style=discord.ButtonStyle.secondary)
        self.prev_button = discord.ui.Button(label="Prev", emoji="◀️", style=discord.ButtonStyle.secondary)
        self.refresh_button = discord.ui.Button(label="Refresh", emoji=Icon.REFRESH, style=discord.ButtonStyle.secondary)
        self.next_button = discord.ui.Button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
        self.last_button = discord.ui.Button(label="Last", emoji="⏭️", style=discord.ButtonStyle.secondary)
        self.first_button.callback = self._first
        self.prev_button.callback = self._prev
        self.refresh_button.callback = self._refresh
        self.next_button.callback = self._next
        self.last_button.callback = self._last

        container = discord.ui.Container(
            self.header, discord.ui.Separator(), self.body, color=discord.Color.blue()
        )
        row = discord.ui.ActionRow(
            self.first_button, self.prev_button, self.refresh_button, self.next_button, self.last_button
        )
        super().__init__(container, row, timeout=180)
        self._update_buttons()

    @property
    def max_page(self) -> int:
        return max(0, (len(self.moderations) - 1) // MODERATIONS_PER_PAGE)

    def _update_buttons(self) -> None:
        self.first_button.disabled = self.prev_button.disabled = self.page == 0
        self.last_button.disabled = self.next_button.disabled = self.page == self.max_page

    def _header_text(self) -> str:
        text = f"## {Icon.MODERATIONS} Moderations for {self.target}"
        if self.moderations:
            text += f"\n-# Page {self.page + 1}/{self.max_page + 1}"
        return text

    def _body_text(self) -> str:
        start = self.page * MODERATIONS_PER_PAGE
        page_items = self.moderations[start : start + MODERATIONS_PER_PAGE]
        if not page_items:
            return "No moderations found."
        return "\n\n".join(f"**#{m.id}**\n{describe_case_line(m)}" for m in page_items)

    async def _refresh_view(self, interaction: discord.Interaction) -> None:
        self.header.content = self._header_text()
        self.body.content = self._body_text()
        self._update_buttons()
        await interaction.response.edit_message(view=self)

    async def _first(self, interaction: discord.Interaction) -> None:
        self.page = 0
        await self._refresh_view(interaction)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self._refresh_view(interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await self._refresh_view(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.max_page, self.page + 1)
        await self._refresh_view(interaction)

    async def _last(self, interaction: discord.Interaction) -> None:
        self.page = self.max_page
        await self._refresh_view(interaction)
