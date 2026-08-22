"""Duplicate-moderation confirmation prompt, mirroring modbot's
UserCommand.preventDuplicateModeration / ConfirmationEmbed / Confirmation.js.

Simplified vs. modbot: the confirm/cancel callback is bound directly to a pycord View
tied to the interaction message, rather than round-tripped through a DB-persisted JSON
blob keyed by a generic customId string. modbot needs that indirection because a
confirmation can be actioned from a different sharded Node process than the one that
created it; pycord runs single-process (per the AutoShardedBot decision), so a bound
closure is simpler and more idiomatic here. The confirmations table/CRUD in models.py
still exists for the intervals cog's cleanup sweep and observability, but nothing here
depends on replaying it after a restart — a restart within the confirmation's lifetime
just loses that one pending prompt, same as any other in-flight interaction would.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

import discord

from ..db import Database
from ..ui import LayoutView
from .models import (
    CONFIRMATION_DURATION,
    MODERATION_WARN_DURATION,
    Confirmation,
    Moderation,
    ModerationTarget,
    delete_confirmation,
    describe_case_line,
    get_recent_by_others,
)

RECENT_MODERATIONS_SHOWN = 3


async def find_duplicate_moderations(
    db: Database, guild_id: int, user_id: int, invoking_moderator_id: int
) -> list[Moderation]:
    since = int(time.time()) - MODERATION_WARN_DURATION
    return await get_recent_by_others(db, guild_id, user_id, invoking_moderator_id, since)


def build_duplicate_warning_container(
    target: ModerationTarget, recent: list[Moderation]
) -> discord.ui.Container:
    lines = [describe_case_line(m) for m in recent[:RECENT_MODERATIONS_SHOWN]]
    return discord.ui.Container(
        discord.ui.TextDisplay("**Possible duplicate moderation**"),
        discord.ui.TextDisplay(
            f"{target.mention} was recently moderated by someone else. Continue anyway?"
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay("\n\n".join(lines)),
        color=discord.Color.orange(),
    )


class ConfirmationView(LayoutView):
    def __init__(
        self,
        db: Database,
        confirmation: Confirmation,
        on_confirm: Callable[[], Awaitable[None]],
        container: discord.ui.Container,
        *,
        danger: bool = False,
    ):
        self.db = db
        self.confirmation = confirmation
        self.message: discord.Message | discord.WebhookMessage | None = None
        self._on_confirm = on_confirm

        confirm_button = discord.ui.Button(
            label="Confirm",
            style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.success,
        )
        confirm_button.callback = self._confirm
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = self._cancel

        super().__init__(container, discord.ui.ActionRow(confirm_button, cancel_button), timeout=CONFIRMATION_DURATION)

    @staticmethod
    def _status_view(text: str) -> LayoutView:
        return LayoutView(discord.ui.TextDisplay(text))

    async def _confirm(self, interaction: discord.Interaction) -> None:
        self.stop()
        await delete_confirmation(self.db, self.confirmation.id)
        await interaction.response.edit_message(view=self._status_view("Confirmed, proceeding..."))
        await self._on_confirm()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await delete_confirmation(self.db, self.confirmation.id)
        await interaction.response.edit_message(view=self._status_view("Cancelled."))

    async def on_timeout(self) -> None:
        await delete_confirmation(self.db, self.confirmation.id)
        if self.message is not None:
            try:
                await self.message.edit(view=self._status_view("This confirmation has expired."))
            except discord.HTTPException:
                pass
