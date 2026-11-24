from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import discord


class ActionError(ValueError):
    """Raised with a message that's safe to show the user."""


@dataclass(frozen=True)
class Action:
    name: str
    # "{a}" and "{t}" are the author and target names, already bolded.
    targeted: str
    solo: Optional[str] = None  # set when the target is optional

    @property
    def needs_target(self) -> bool:
        return self.solo is None


def _feeling(name: str, text: str) -> Action:
    return Action(name, targeted=f"{{a}} {text} because of {{t}}", solo=f"{{a}} {text}")


ACTIONS: dict[str, Action] = {
    a.name: a
    for a in (
        Action("hug", "{a} hugged {t}"),
        Action("bonk", "{a} bonked {t}"),
        Action("boop", "{a} booped {t}"),
        Action("kiss", "{a} kissed {t}"),
        Action("lick", "{a} licked {t}"),
        Action("bellyrub", "{a} gave {t} bellyrubs"),
        Action("cuddle", "{a} cuddled {t}"),
        Action("highfive", "{a} high-fived {t}"),
        Action("rawr", "{a} rawred at {t}"),
        Action("howl", "{a} howled at {t}. Awoo!", solo="{a} howls. Awoo!"),
        Action("pat", "{a} patted {t}"),
        _feeling("blush", "blushes"),
        _feeling("happy", "is happy"),
        _feeling("wag", "wags their tail"),
    )
}


def _bold(name: str) -> str:
    return f"**{discord.utils.escape_markdown(name)}**"


def describe(action: Action, author: str, target: Optional[str], reason: Optional[str]) -> str:
    if target is None and action.needs_target:
        raise ActionError(f"Pick someone to {action.name}.")
    template = action.targeted if target is not None else action.solo
    text = template.format(a=_bold(author), t=_bold(target) if target is not None else "")
    if reason:
        text += f"\n**Reason:** {reason}"
    return text
