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
    # Footer for repeat counts: plain names plus "{n}" ("once", "twice", "3 times").
    tally: str
    solo: Optional[str] = None  # set when the target is optional

    @property
    def needs_target(self) -> bool:
        return self.solo is None


def _done(name: str, verb: str) -> Action:
    return Action(name, targeted=f"{{a}} {verb} {{t}}", tally=f"{{a}} has {verb} {{t}} {{n}}")


def _feeling(name: str, text: str, base: str) -> Action:
    return Action(
        name, targeted=f"{{a}} {text} because of {{t}}", solo=f"{{a}} {text}",
        tally=f"{{t}} has made {{a}} {base} {{n}}",
    )


ACTIONS: dict[str, Action] = {
    a.name: a
    for a in (
        _done("hug", "hugged"),
        _done("bonk", "bonked"),
        _done("boop", "booped"),
        _done("kiss", "kissed"),
        _done("lick", "licked"),
        Action("bellyrub", "{a} gave {t} bellyrubs", tally="{a} has given {t} bellyrubs {n}"),
        _done("cuddle", "cuddled"),
        _done("highfive", "high-fived"),
        _done("rawr", "rawred at"),
        Action("howl", "{a} howled at {t}. Awoo!", tally="{a} has howled at {t} {n}", solo="{a} howls. Awoo!"),
        _done("pat", "patted"),
        _feeling("blush", "blushes", "blush"),
        _feeling("happy", "is happy", "happy"),
        _feeling("wag", "wags their tail", "wag their tail"),
    )
}


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def tally(action: Action, author: str, target: str, count: int) -> str:
    esc = discord.utils.escape_markdown
    return action.tally.format(a=esc(author), t=esc(target), n=_times(count))


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
