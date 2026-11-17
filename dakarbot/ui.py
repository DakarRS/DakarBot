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
from typing import Sequence

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


class Icon:
    """Plain unicode stand-ins for modbot's config-driven custom emoji (its
    inlineEmojiIfExists/componentEmojiIfExists calls reference per-bot custom emoji
    IDs that don't exist in this port) — used the same way modbot uses them: as a
    prefix on headings, key-value fields, and button labels, not sprinkled into every
    line of body text."""

    # generic
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    REFRESH = "🔄"
    DELETE = "🗑️"
    ID = "🆔"
    CREATED = "📅"
    JOINED = "📥"

    # moderation domain
    MODERATIONS = "📋"
    AVATAR = "🖼️"
    LOCK = "🔒"
    UNLOCK = "🔓"

    # server/settings
    SERVER = "🏠"
    ROLE = "🎭"
    SEARCH = "🔍"
    SHIELD = "🛡️"
    AUTOMOD = "🤖"
    LADDER = "⚖️"
    BAD_WORD = "🚫"
    AUTO_RESPONSE = "💬"
    EXPORT = "📤"
    IMPORT = "📥"


INTERACTION_FAILED = f"{Icon.ERROR} Something went wrong. The error has been reported."
