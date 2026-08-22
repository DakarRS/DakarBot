"""One module per command, each exposing a single async run(cog, ctx, ...) function.
base.py's thin command methods (the ones pycord actually needs the Option-annotated
signatures on) just call these directly, e.g. `await commands.ban.run(self, ctx, ...)`.
"""
from . import (
    ban,
    clear,
    delete,
    edit,
    kick,
    list_cases,
    mute,
    pardon,
    show,
    soft_ban,
    strike,
    strike_purge,
    unban,
    unmute,
)

__all__ = [
    "ban",
    "clear",
    "delete",
    "edit",
    "kick",
    "list_cases",
    "mute",
    "pardon",
    "show",
    "soft_ban",
    "strike",
    "strike_purge",
    "unban",
    "unmute",
]
