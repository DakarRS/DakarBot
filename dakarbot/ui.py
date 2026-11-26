"""Shared Components V2 UI base.

Every message in DakarBot is built from Components V2 layout primitives (Container,
Section, TextDisplay, Separator) instead of discord.Embed — Discord's newer, more
flexible message-layout system, which pycord fully supports natively.

Two things confirmed by inspecting pycord's own implementation before relying on them:

- discord.ui.View hardcodes is_components_v2() to False — it's explicitly the
  "legacy V1" base for Button/Select-only UIs and can never carry V2 components.
  discord.ui.BaseView is the actual base that supports both, but it has no
  MAX_ITEMS set (it's meant to be subclassed, not instantiated directly) — LayoutView
  just fills that in.
- BaseView/add_item does NOT auto-wrap bare buttons into an action row the way the
  legacy View does. Confirmed by inspecting the serialized payload: a bare Button
  passed directly produces an invalid top-level `type: 2` component. Any interactive
  items must be wrapped in an explicit discord.ui.ActionRow(...).
- BaseView also has no `_refresh()` — the method Messageable.send() calls after
  sending to reconcile a view's items with the components Discord echoes back
  (e.g. filling in details on dispatchable items). It's only implemented on View
  and DesignerView, not the shared base; omitting it crashes send() with
  AttributeError. View's own _refresh rebuilds self.children as a flat list, which
  would destroy LayoutView's nested Container > Section > ActionRow structure, so
  it can't just be copied — see below for a version that walks the nested tree
  (via the public walk_children(), which already descends into Container/Section)
  and updates matching items in place instead of flattening/replacing the tree.
"""

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Sequence

import discord
from discord.ui.view import _walk_all_components_v2

from .error_reporting import describe_interaction, tell_user

logger = logging.getLogger("dakarbot.ui")


class LayoutView(discord.ui.BaseView):
    MAX_ITEMS = 25

    def _refresh(self, components: list) -> None:
        current = {
            (item.type.value, item.custom_id): item
            for item in self.walk_children()
            if item.is_dispatchable()
        }
        for component in _walk_all_components_v2(components):
            key = (component.type.value, getattr(component, "custom_id", None))
            existing = current.get(key)
            if existing is not None:
                existing.refresh_component(component)

    async def on_error(self, error: Exception, item: discord.ui.Item, interaction: discord.Interaction) -> None:
        label = getattr(item, "label", None) or getattr(item, "custom_id", None) or type(item).__name__
        await report_interaction_error(f"{type(self).__name__} button `{label}`", error, interaction)


class ConfirmView(LayoutView):
    """Confirm/Cancel under a container. Only `user_id` (when given) can answer."""

    def __init__(
        self,
        container: discord.ui.Container,
        on_confirm: Callable[[], Awaitable[None]],
        *,
        danger: bool = False,
        timeout: float = 60,
        user_id: Optional[int] = None,
    ):
        self.message: discord.Message | discord.WebhookMessage | None = None
        self._on_confirm = on_confirm
        self._user_id = user_id

        confirm_button = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.success
        )
        confirm_button.callback = self._confirm
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = self._cancel
        super().__init__(container, discord.ui.ActionRow(confirm_button, cancel_button), timeout=timeout)

    async def on_resolved(self) -> None:
        """Runs once, however the prompt ends (confirm, cancel or timeout)."""

    @staticmethod
    def _status_view(text: str) -> LayoutView:
        return LayoutView(discord.ui.TextDisplay(text))

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if self._user_id is not None and interaction.user.id != self._user_id:
            await interaction.response.send_message("This prompt isn't for you.", ephemeral=True)
            return False
        return True

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if not await self._allowed(interaction):
            return
        self.stop()
        await self.on_resolved()
        await interaction.response.edit_message(view=self._status_view("Confirmed, proceeding..."))
        await self._on_confirm()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if not await self._allowed(interaction):
            return
        self.stop()
        await self.on_resolved()
        await interaction.response.edit_message(view=self._status_view("Cancelled."))

    async def on_timeout(self) -> None:
        await self.on_resolved()
        if self.message is not None:
            try:
                await self.message.edit(view=self._status_view("This confirmation has expired."))
            except discord.HTTPException:
                pass


async def report_interaction_error(what: str, error: Exception, interaction: discord.Interaction) -> None:
    logger.error("%s failed %s", what, describe_interaction(interaction), exc_info=error)
    await tell_user(interaction, INTERACTION_FAILED)


def format_list_or_inline(items: Sequence[str], threshold: int = 3) -> str:
    """A handful of items reads fine comma-separated on one line; more than a
    handful gets lost that way, so it switches to a real bullet list — matches
    modbot's own KeyValueEmbed.addListOrShortList."""
    items = list(items)
    if not items:
        return "None"
    if len(items) <= threshold:
        return ", ".join(items)
    return "\n".join(f"- {item}" for item in items)


INTERACTION_FAILED = "Something went wrong. The error has been reported."


def timestamp(unix: int, style: str = "R") -> str:
    """A Discord timestamp tag (shown in each viewer's own time zone) for a Unix time."""
    return discord.utils.format_dt(datetime.fromtimestamp(unix, timezone.utc), style)
