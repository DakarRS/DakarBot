"""Background interval tasks.

Ports modbot's src/interval/UnbanInterval.js and UnmuteInterval.js as a single pycord
tasks.loop background task, on the same 30s cadence. TransferMuteToTimeoutInterval and
CleanupConfirmationInterval land later, once the settings/automod machinery they
depend on exists.

Expiry goes through the same ModeratedMember.unban()/unmute() the /unban and /unmute
commands use (not a bare DB update), so an auto-expired ban gets a proper `unban` case
record too, with the bot itself as moderator — matching modbot's own approach of
reusing MemberWrapper for interval-driven actions.
"""

import logging

import discord
from discord.ext import tasks

from ..bot import DakarBot, DakarCog
from ..moderation import models
from ..moderation.models import ModeratedMember, Moderation, ModerationAction

logger = logging.getLogger("dakarbot.intervals")

CHECK_INTERVAL_SECONDS = 30


class IntervalsCog(DakarCog, name="Intervals"):
    def __init__(self, bot: DakarBot):
        super().__init__(bot)
        self.expire_moderations.start()

    def cog_unload(self) -> None:
        self.expire_moderations.cancel()

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def expire_moderations(self) -> None:
        now_ts = int(discord.utils.utcnow().timestamp())
        for action in (ModerationAction.BAN, ModerationAction.MUTE):
            expired = await models.get_expired_active(self.db, action, now_ts)
            for record in expired:
                await self._expire_one(record)

    @expire_moderations.before_loop
    async def before_expire_moderations(self) -> None:
        await self.bot.wait_until_ready()

    async def _expire_one(self, record: Moderation) -> None:
        guild = self.bot.get_guild(record.guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(record.guild_id)
            except discord.HTTPException:
                logger.warning(
                    "Guild %s not found while expiring moderation #%s", record.guild_id, record.id
                )
                return

        try:
            user = await self.bot.fetch_user(record.user_id)
        except discord.NotFound:
            # User account no longer exists at all — nothing to reverse on Discord's
            # side, just stop tracking it as active.
            await models.disable_active(self.db, record.guild_id, record.user_id, record.action)
            return

        moderated = ModeratedMember(self.db, self.guild_settings, guild, user)
        moderator_id = self.bot.user.id if self.bot.user else None

        try:
            if record.action is ModerationAction.BAN:
                await moderated.unban("Temporary ban expired.", None, moderator_id)
            elif record.action is ModerationAction.MUTE:
                await moderated.unmute("Temporary mute expired.", None, moderator_id)
        except discord.HTTPException:
            logger.exception("Failed to auto-expire moderation #%s", record.id)


def setup(bot: DakarBot) -> None:
    bot.add_cog(IntervalsCog(bot))
