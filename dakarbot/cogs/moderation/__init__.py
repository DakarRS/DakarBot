"""Moderation cog package: ban/unban/soft-ban/kick/mute/unmute/strike/pardon/
strike-purge, plus the /moderation show/edit/delete/list/clear command group.

See base.py for the actual cog class and command declarations, and commands/ for
each command's full implementation (including, for ban/kick/mute/strike, that
command's own context-menu modal and, for list, its paginated case-list view).

Note the naming overlap: this package is dakarbot.cogs.moderation (the loadable
cog), distinct from dakarbot.moderation (the domain package — models, confirmation,
duration) that it and every other cog import from.
"""

from dakarbot.bot import DakarBot

from .base import ModerationCog


def setup(bot: DakarBot) -> None:
    bot.add_cog(ModerationCog(bot))
