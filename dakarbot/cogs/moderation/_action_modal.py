"""Shared base for the ban/kick/mute/strike context-menu modals — just the
reason/comment fields and ctx-building boilerplate every one of them needs (mirrors
modbot's own shared ReasonInput/CommentInput modal-field components). Each concrete
modal (BanModal, KickModal, ...) lives in its own command file alongside the rest of
that command's logic, not here.
"""
from typing import TYPE_CHECKING, Optional, Union

import discord

if TYPE_CHECKING:
    from .base import ModerationCog


class ActionModal(discord.ui.Modal):
    def __init__(
        self, cog: "ModerationCog", target: Union[discord.Member, discord.User], action_label: str
    ):
        super().__init__(title=f"{action_label.title()} {target.display_name}"[:45])
        self.cog = cog
        self.target = target
        self.reason_input = discord.ui.InputText(label="Reason", required=False, max_length=500)
        self.comment_input = discord.ui.InputText(
            label="Comment (internal, not shown to user)", required=False, max_length=500
        )
        self.add_item(self.reason_input)
        self.add_item(self.comment_input)

    @property
    def reason(self) -> str:
        return self.reason_input.value or "No reason provided."

    @property
    def comment(self) -> Optional[str]:
        return self.comment_input.value or None

    def application_context(self, interaction: discord.Interaction) -> discord.ApplicationContext:
        return self.cog.bot.get_application_context(interaction)
