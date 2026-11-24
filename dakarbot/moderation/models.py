"""Everything about the moderation domain: the DB model and queries, the punishment
ladder, display formatting, and ModeratedMember (the class that actually performs
ban/kick/mute/etc. and records them) — all in one file rather than split across
models.py/punishment.py/formatting.py/member.py.

That split looked clean on paper but wasn't: ModeratedMember._log_case needs the
formatting functions, which need the Moderation type, which was already here — a
straight circular import once member.py and formatting.py were separate files. And
every one of those "separate" files was only ever used alongside this one anyway
(the DB layer, the actions that produce DB rows, and the display of those rows are
one cohesive domain, not several). ModeratedMember mirrors modbot's MemberWrapper.js;
where the target has a live discord.Member (in the guild, cache/fetch succeeded), the
actual Discord-side action is performed; otherwise most actions still record to the
DB (matching modbot), except kick, which silently no-ops entirely if the member can't
be fetched — no Discord call, no DB record.

confirmation.py stays separate: the duplicate-moderation warning + its View is a
distinct, self-contained feature (not moderation *data*), same reasoning duration.py
(a generic string-parsing utility) stays separate too.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Union

import discord

from ..db import Database
from ..ui import LayoutView
from .duration import format_duration

if TYPE_CHECKING:
    # Only for type hints — guild_settings.py imports FROM this module already, so a
    # real runtime import here would be circular. Safe under `from __future__ import
    # annotations`, which makes every annotation a lazy string never evaluated.
    from ..guild_settings import GuildSettings

logger = logging.getLogger("dakarbot.moderation")

# A moderation target: a live guild Member, or just a User (e.g. a banned account
# that's no longer a member). Used throughout the moderation domain.
ModerationTarget = Union[discord.Member, discord.User]

# Action types that count as a "duplicate moderation" trigger for the confirmation
# prompt. unban/unmute/pardon deliberately don't count.
DUPLICATE_TRIGGER_ACTIONS = ("kick", "ban", "mute", "strike", "softban")

# Trigger window for the duplicate-moderation confirmation prompt.
MODERATION_WARN_DURATION = 5 * 60

# How long a pending confirmation (button) stays valid before the intervals cog's
# cleanup sweep would sweep it.
CONFIRMATION_DURATION = 15 * 60

# Discord's own timeout ceiling; longer mutes must use a muted role instead.
TIMEOUT_LIMIT = 28 * 24 * 3600

# Discord's own limit on how far back a ban can purge messages.
BAN_MESSAGE_DELETE_LIMIT = 7 * 24 * 3600

# Default message-history window for /ban and /soft-ban when no `delete` option given.
DEFAULT_DELETE_MESSAGE_SECONDS = 3600

MODERATIONS_PER_PAGE = 20


class ModerationAction(str, Enum):
    BAN = "ban"
    UNBAN = "unban"
    KICK = "kick"
    MUTE = "mute"
    UNMUTE = "unmute"
    SOFTBAN = "softban"
    STRIKE = "strike"
    PARDON = "pardon"


@dataclass
class Moderation:
    id: Optional[int]
    guild_id: int
    user_id: int
    action: ModerationAction
    created: int
    value: int = 0
    expire_time: Optional[int] = None
    reason: str = "No reason provided."
    comment: Optional[str] = None
    moderator: Optional[int] = None
    active: bool = True

    @property
    def duration(self) -> Optional[int]:
        if self.expire_time is None:
            return None
        return self.expire_time - self.created

    @classmethod
    def _from_row(cls, row: dict[str, Any]) -> "Moderation":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            action=ModerationAction(row["action"]),
            created=row["created"],
            value=row["value"],
            expire_time=row["expire_time"],
            reason=row["reason"] or "No reason provided.",
            comment=row["comment"],
            moderator=row["moderator"],
            active=bool(row["active"]),
        )


class PunishmentAction(str, Enum):
    NONE = "none"
    STRIKE = "strike"
    MUTE = "mute"
    SOFTBAN = "softban"
    BAN = "ban"
    KICK = "kick"


@dataclass
class Punishment:
    action: PunishmentAction
    duration: Optional[int] = None
    count: Optional[int] = None


# Keyed by exact strike-count threshold. 4 and 10 intentionally have no entry — see
# find_punishment's fallback behavior below.
DEFAULT_LADDER: dict[int, Punishment] = {
    1: Punishment(PunishmentAction.MUTE, duration=5 * 60),
    2: Punishment(PunishmentAction.MUTE, duration=30 * 60),
    3: Punishment(PunishmentAction.MUTE, duration=3 * 3600),
    5: Punishment(PunishmentAction.BAN, duration=1 * 24 * 3600),
    6: Punishment(PunishmentAction.BAN, duration=7 * 24 * 3600),
    7: Punishment(PunishmentAction.BAN, duration=14 * 24 * 3600),
    8: Punishment(PunishmentAction.BAN, duration=60 * 24 * 3600),
    9: Punishment(PunishmentAction.BAN, duration=180 * 24 * 3600),
    11: Punishment(PunishmentAction.BAN, duration=None),
}


def find_punishment(ladder: dict[int, Punishment], strikes: int) -> Optional[Punishment]:
    """Walk downward from `strikes` to the nearest defined threshold at or below it —
    this is NOT an exact-match lookup.

    This deliberately re-triggers the same punishment again if the current total isn't
    itself a defined key (e.g. reaching 4 strikes re-fires the level-3 punishment, even
    though it already fired at 3). That's modbot's existing behavior, ported exactly —
    not a bug to silently fix.
    """
    while strikes > 0:
        punishment = ladder.get(strikes)
        if punishment:
            return punishment
        strikes -= 1
    return None


async def create(db: Database, moderation: Moderation) -> Moderation:
    # Disable any prior active moderation of the same action type for this user first,
    # matching MemberWrapper.createModeration's call to disableActiveModerations.
    await disable_active(db, moderation.guild_id, moderation.user_id, moderation.action)
    new_id = await db.insert(
        """
        INSERT INTO moderations
            (guild_id, user_id, action, created, value, expire_time, reason, comment, moderator, active)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        moderation.guild_id,
        moderation.user_id,
        moderation.action.value,
        moderation.created,
        moderation.value,
        moderation.expire_time,
        moderation.reason,
        moderation.comment,
        moderation.moderator,
        moderation.active,
    )
    moderation.id = new_id
    return moderation


async def disable_active(db: Database, guild_id: int, user_id: int, action: ModerationAction) -> None:
    await db.execute(
        "UPDATE moderations SET active = FALSE"
        " WHERE active = TRUE AND guild_id = $1 AND user_id = $2 AND action = $3",
        guild_id,
        user_id,
        action.value,
    )


async def get(db: Database, guild_id: int, moderation_id: int) -> Optional[Moderation]:
    row = await db.fetchone(
        "SELECT * FROM moderations WHERE guild_id = $1 AND id = $2", guild_id, moderation_id
    )
    return Moderation._from_row(row) if row else None


async def get_active(
    db: Database, guild_id: int, user_id: int, action: ModerationAction
) -> Optional[Moderation]:
    row = await db.fetchone(
        "SELECT * FROM moderations WHERE guild_id = $1 AND user_id = $2 AND action = $3 AND active = TRUE"
        " ORDER BY created DESC LIMIT 1",
        guild_id,
        user_id,
        action.value,
    )
    return Moderation._from_row(row) if row else None


async def get_all_active(db: Database, guild_id: int, action: ModerationAction) -> list[Moderation]:
    """Every active moderation of this type in a guild — for the settings cog's
    muted-role migration when the muted role is changed."""
    rows = await db.fetchall(
        "SELECT * FROM moderations WHERE guild_id = $1 AND action = $2 AND active = TRUE",
        guild_id,
        action.value,
    )
    return [Moderation._from_row(row) for row in rows]


async def get_expired_active(db: Database, action: ModerationAction, now: int) -> list[Moderation]:
    """All still-active moderations of this type, across every guild, whose expiry has
    passed — for the intervals cog's expiry sweep."""
    rows = await db.fetchall(
        "SELECT * FROM moderations"
        " WHERE action = $1 AND active = TRUE AND expire_time IS NOT NULL AND expire_time <= $2",
        action.value,
        now,
    )
    return [Moderation._from_row(row) for row in rows]


async def get_strike_sum(db: Database, guild_id: int, user_id: int) -> int:
    row = await db.fetchone(
        "SELECT SUM(value) AS total FROM moderations"
        " WHERE guild_id = $1 AND user_id = $2 AND action IN ($3, $4)",
        guild_id,
        user_id,
        ModerationAction.STRIKE.value,
        ModerationAction.PARDON.value,
    )
    return int(row["total"]) if row and row["total"] is not None else 0


async def get_recent_by_others(
    db: Database, guild_id: int, user_id: int, exclude_moderator: int, since: int
) -> list[Moderation]:
    """Moderations against this user since `since` by someone other than
    exclude_moderator, restricted to the action types that trigger the duplicate-
    moderation confirmation prompt."""
    action_placeholders = ", ".join(f"${i + 5}" for i in range(len(DUPLICATE_TRIGGER_ACTIONS)))
    rows = await db.fetchall(
        """
        SELECT * FROM moderations
        WHERE guild_id = $1 AND user_id = $2 AND created >= $3
          AND (moderator IS NULL OR moderator != $4)
          AND action IN ({action_placeholders})
        ORDER BY created DESC
        """,
        guild_id,
        user_id,
        since,
        exclude_moderator,
        *DUPLICATE_TRIGGER_ACTIONS,
    )
    return [Moderation._from_row(row) for row in rows]


async def list_for_user(db: Database, guild_id: int, user_id: int) -> list[Moderation]:
    rows = await db.fetchall(
        "SELECT * FROM moderations WHERE guild_id = $1 AND user_id = $2 ORDER BY created ASC",
        guild_id,
        user_id,
    )
    return [Moderation._from_row(row) for row in rows]


async def update(db: Database, moderation: Moderation) -> None:
    await db.execute(
        "UPDATE moderations SET reason = $1, comment = $2, expire_time = $3, value = $4 WHERE id = $5",
        moderation.reason,
        moderation.comment,
        moderation.expire_time,
        moderation.value,
        moderation.id,
    )


async def delete(db: Database, guild_id: int, moderation_id: int) -> int:
    return await db.execute(
        "DELETE FROM moderations WHERE guild_id = $1 AND id = $2", guild_id, moderation_id
    )


async def clear_for_user(db: Database, guild_id: int, user_id: int) -> int:
    return await db.execute(
        "DELETE FROM moderations WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
    )


async def count_for_user(db: Database, guild_id: int, user_id: int) -> int:
    row = await db.fetchone(
        "SELECT COUNT(*) AS total FROM moderations WHERE guild_id = $1 AND user_id = $2",
        guild_id,
        user_id,
    )
    return int(row["total"]) if row else 0


async def search_by_id(db: Database, guild_id: int, partial_id: str, limit: int = 5) -> list[Moderation]:
    rows = await db.fetchall(
        "SELECT * FROM moderations WHERE guild_id = $1 AND id::text LIKE $2 ORDER BY id DESC LIMIT $3",
        guild_id,
        f"%{partial_id}%",
        limit,
    )
    return [Moderation._from_row(row) for row in rows]


async def search_by_moderator(
    db: Database, guild_id: int, moderator_id: int, limit: int = 5
) -> list[Moderation]:
    rows = await db.fetchall(
        "SELECT * FROM moderations WHERE guild_id = $1 AND moderator = $2 ORDER BY created DESC LIMIT $3",
        guild_id,
        moderator_id,
        limit,
    )
    return [Moderation._from_row(row) for row in rows]


@dataclass
class Confirmation:
    id: Optional[int]
    data: dict[str, Any]
    expires: int

    @classmethod
    def _from_row(cls, row: dict[str, Any]) -> "Confirmation":
        return cls(id=row["id"], data=json.loads(row["data"]), expires=row["expires"])


async def create_confirmation(
    db: Database, data: dict[str, Any], ttl: int = CONFIRMATION_DURATION
) -> Confirmation:
    expires = int(time.time()) + ttl
    new_id = await db.insert(
        "INSERT INTO confirmations (data, expires) VALUES ($1, $2) RETURNING id",
        json.dumps(data),
        expires,
    )
    return Confirmation(id=new_id, data=data, expires=expires)


async def get_confirmation(db: Database, confirmation_id: int) -> Optional[Confirmation]:
    row = await db.fetchone("SELECT * FROM confirmations WHERE id = $1", confirmation_id)
    return Confirmation._from_row(row) if row else None


async def delete_confirmation(db: Database, confirmation_id: int) -> None:
    await db.execute("DELETE FROM confirmations WHERE id = $1", confirmation_id)


async def delete_expired_confirmations(db: Database) -> int:
    return await db.execute("DELETE FROM confirmations WHERE expires <= $1", int(time.time()))


# ---- display formatting ----

# Matches modbot's util/colors.js resolveColor(): ban is the one irreversible-by-
# default action (red), unban/unmute/pardon are all "this is getting better" (green),
# and everything else is a punitive-but-reversible action (orange) — colors by what
# the action *is*, not by whether the case happens to still be active right now.
_ACTION_COLORS: dict[ModerationAction, discord.Color] = {
    ModerationAction.BAN: discord.Color.red(),
    ModerationAction.KICK: discord.Color.orange(),
    ModerationAction.MUTE: discord.Color.orange(),
    ModerationAction.SOFTBAN: discord.Color.orange(),
    ModerationAction.STRIKE: discord.Color.orange(),
    ModerationAction.UNBAN: discord.Color.green(),
    ModerationAction.UNMUTE: discord.Color.green(),
    ModerationAction.PARDON: discord.Color.green(),
}


def resolve_action_color(action: ModerationAction) -> discord.Color:
    return _ACTION_COLORS.get(action, discord.Color.blue())


def describe_case_line(moderation: Moderation) -> str:
    """One-line summary of a case: action, moderator, when, strike/pardon value,
    duration, and a truncated reason — used anywhere a case gets listed rather than
    shown on its own (the /moderation list pages, the duplicate-moderation warning)."""
    line = f"**{moderation.action.value}** by <@{moderation.moderator}> <t:{moderation.created}:R>"
    if moderation.action in (ModerationAction.STRIKE, ModerationAction.PARDON):
        line += f" ({moderation.value:+d})"
    if moderation.duration:
        line += f" for {format_duration(moderation.duration)}"
    reason = (moderation.reason or "")[:200]
    if reason:
        line += f"\n{reason}"
    return line


def build_case_container(moderation: Moderation) -> discord.ui.Container:
    detail_lines = [
        f"**Moderator:** {f'<@{moderation.moderator}>' if moderation.moderator else 'Unknown'}",
        f"**Created:** <t:{moderation.created}:f>",
    ]
    if moderation.duration:
        detail_lines.append(f"**Duration:** {format_duration(moderation.duration)}")
    if moderation.action in (ModerationAction.STRIKE, ModerationAction.PARDON):
        detail_lines.append(f"**Value:** {moderation.value:+d}")
    if moderation.comment:
        detail_lines.append(f"**Comment:** {moderation.comment}")

    return discord.ui.Container(
        discord.ui.TextDisplay(f"## Case #{moderation.id} — {moderation.action.value.upper()}"),
        discord.ui.TextDisplay(f"<@{moderation.user_id}>\n{moderation.reason}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay("\n".join(detail_lines)),
        discord.ui.TextDisplay("-# Active" if moderation.active else "-# Inactive"),
        color=resolve_action_color(moderation.action),
    )


def build_case_view(moderation: Moderation) -> LayoutView:
    return LayoutView(build_case_container(moderation))


# ---- ModeratedMember: performs ban/kick/mute/etc. and records them ----


class ModeratedMember:
    def __init__(
        self,
        db: Database,
        guild_settings: "GuildSettings",
        guild: discord.Guild,
        user: ModerationTarget,
        member: Optional[discord.Member] = None,
    ):
        self.db = db
        self.guild_settings = guild_settings
        self.guild = guild
        self.user = user
        # Resolves a live Member from the guild's cache when one isn't passed in and
        # `user` isn't already one (e.g. a banned user who isn't currently a member) —
        # every caller needs this same resolution, so it lives here once instead of
        # being repeated at each call site.
        if member is not None:
            self.member = member
        elif isinstance(user, discord.Member):
            self.member = user
        else:
            self.member = guild.get_member(user.id)

    async def get_strike_sum(self) -> int:
        return await get_strike_sum(self.db, self.guild.id, self.user.id)

    @staticmethod
    def _shorten_reason(reason: str) -> str:
        # modbot's JS shortenReason has an operator-precedence bug
        # (`reason.substring(0,500) + reason.length > 500 ? '...' : ''`) that discards
        # the substring almost always. This ports the obviously-intended behavior
        # instead: truncate to 500 chars, append "..." only if it was truncated.
        return reason[:500] + ("..." if len(reason) > 500 else "")

    async def _dm(self, verb: str, reason: str, duration: Optional[int], preposition: str) -> None:
        message = f"You have been {verb} {preposition} **{self.guild.name}**"
        if duration:
            message += f" for {format_duration(duration)}"
        message += f" | {reason}"
        try:
            await self.user.send(message)
        except discord.HTTPException:
            pass  # DMs are best-effort; never block the moderation action on failure

    # Recording one of these actions also deactivates any active row of the paired
    # "opposite" type — create() only auto-deactivates same-type rows (e.g. a new
    # mute superseding an old one), so without this an unmute/unban would leave the
    # mute/ban it's reversing stuck active=TRUE forever.
    _OPPOSITE_ACTION = {
        ModerationAction.UNBAN: ModerationAction.BAN,
        ModerationAction.UNMUTE: ModerationAction.MUTE,
    }

    async def record(
        self,
        action: ModerationAction,
        reason: str,
        comment: Optional[str],
        moderator_id: Optional[int],
        value: int = 0,
        expire_time: Optional[int] = None,
    ) -> Moderation:
        """Creates the DB moderation row and posts the case embed to the guild's log
        channel (matching modbot's Moderation.log(), called from every moderation-
        creating path). Public — also called directly by the logging cog's audit-log
        listener to record actions that already happened outside the bot, without
        the DM/live-Discord-API-call side effects that ban()/kick()/mute()/etc. do.
        """
        opposite = self._OPPOSITE_ACTION.get(action)
        if opposite is not None:
            await disable_active(self.db, self.guild.id, self.user.id, opposite)

        moderation = Moderation(
            id=None,
            guild_id=self.guild.id,
            user_id=self.user.id,
            action=action,
            created=int(time.time()),
            value=value,
            expire_time=expire_time,
            reason=reason or "No reason provided.",
            comment=comment,
            moderator=moderator_id,
            active=True,
        )
        moderation = await create(self.db, moderation)
        await self._log_case(moderation)
        return moderation

    async def _log_case(self, moderation: Moderation) -> None:
        log_channel_id = await self.guild_settings.get_log_channel_id(self.guild.id)
        if not log_channel_id:
            return
        channel = self.guild.get_channel(log_channel_id)
        if channel is None:
            return
        try:
            await channel.send(view=build_case_view(moderation))
        except discord.HTTPException:
            logger.warning("Failed to post case #%s to log channel in guild %s", moderation.id, self.guild.id)

    async def strike(
        self, reason: str, comment: Optional[str], moderator_id: Optional[int], amount: int = 1
    ) -> Moderation:
        await self._dm("striked", reason, None, "in")
        moderation = await self.record(ModerationAction.STRIKE, reason, comment, moderator_id, value=amount)

        total = await self.get_strike_sum()
        ladder = await self.guild_settings.get_punishment_ladder(self.guild.id)
        punishment = find_punishment(ladder, total)
        if punishment:
            await self.execute_punishment(punishment, f"Reaching {total} strikes")

        return moderation

    async def pardon(
        self, reason: str, comment: Optional[str], moderator_id: Optional[int], amount: int = 1
    ) -> Moderation:
        message = f"{amount} strikes have been pardoned in **{self.guild.name}** | {reason}"
        try:
            await self.user.send(message)
        except discord.HTTPException:
            pass
        return await self.record(ModerationAction.PARDON, reason, comment, moderator_id, value=-amount)

    async def ban(
        self,
        reason: str,
        comment: Optional[str],
        moderator_id: Optional[int],
        duration: Optional[int] = None,
        delete_message_seconds: int = DEFAULT_DELETE_MESSAGE_SECONDS,
    ) -> Moderation:
        delete_message_seconds = min(delete_message_seconds, BAN_MESSAGE_DELETE_LIMIT)
        await self._dm("banned", reason, duration, "from")
        await self.guild.ban(
            self.user,
            reason=self._shorten_reason(reason),
            delete_message_seconds=delete_message_seconds,
        )
        expire_time = int(time.time()) + duration if duration else None
        return await self.record(ModerationAction.BAN, reason, comment, moderator_id, expire_time=expire_time)

    async def unban(self, reason: str, comment: Optional[str], moderator_id: Optional[int]) -> Moderation:
        # record() deactivates the active ban row itself (see _OPPOSITE_ACTION).
        try:
            await self.guild.unban(self.user, reason=self._shorten_reason(reason))
        except discord.NotFound:
            pass  # already unbanned in Discord; DB just had a stale active row
        return await self.record(ModerationAction.UNBAN, reason, comment, moderator_id)

    async def softban(
        self,
        reason: str,
        comment: Optional[str],
        moderator_id: Optional[int],
        delete_message_seconds: int = DEFAULT_DELETE_MESSAGE_SECONDS,
    ) -> Moderation:
        delete_message_seconds = min(delete_message_seconds, BAN_MESSAGE_DELETE_LIMIT)
        await self._dm("softbanned", reason, None, "from")
        await self.guild.ban(
            self.user,
            reason=self._shorten_reason(reason),
            delete_message_seconds=delete_message_seconds,
        )
        # Unban directly via the API (not self.unban()) — softban does not disable
        # active ban moderations or create an `unban` row, matching modbot exactly.
        await self.guild.unban(self.user, reason="softban")
        return await self.record(ModerationAction.SOFTBAN, reason, comment, moderator_id)

    async def kick(
        self, reason: str, comment: Optional[str], moderator_id: Optional[int]
    ) -> Optional[Moderation]:
        if self.member is None:
            return None  # matches modbot: silent no-op, no DB record, if unfetchable
        await self._dm("kicked", reason, None, "from")
        await self.member.kick(reason=self._shorten_reason(reason))
        return await self.record(ModerationAction.KICK, reason, comment, moderator_id)

    async def mute(
        self,
        reason: str,
        comment: Optional[str],
        moderator_id: Optional[int],
        duration: Optional[int] = None,
    ) -> Optional[Moderation]:
        use_timeout = bool(duration) and duration <= TIMEOUT_LIMIT
        await self._dm("muted", reason, duration, "in")

        if self.member is not None:
            if use_timeout:
                until = discord.utils.utcnow() + datetime.timedelta(seconds=duration)
                await self.member.timeout(until, reason=self._shorten_reason(reason))
            else:
                muted_role_id = await self.guild_settings.get_muted_role_id(self.guild.id)
                muted_role = self.guild.get_role(muted_role_id) if muted_role_id else None
                if muted_role is None:
                    logger.warning(
                        "Guild %s: can't mute %s, no valid muted role is configured",
                        self.guild.id,
                        self.user.id,
                    )
                    return None
                await self.member.add_roles(muted_role, reason=self._shorten_reason(reason))

        expire_time = int(time.time()) + duration if duration else None
        return await self.record(ModerationAction.MUTE, reason, comment, moderator_id, expire_time=expire_time)

    async def unmute(self, reason: str, comment: Optional[str], moderator_id: Optional[int]) -> Moderation:
        if self.member is not None:
            muted_role_id = await self.guild_settings.get_muted_role_id(self.guild.id)
            muted_role = self.guild.get_role(muted_role_id) if muted_role_id else None
            if muted_role is not None and muted_role in self.member.roles:
                await self.member.remove_roles(muted_role, reason=self._shorten_reason(reason))
            # Unconditional — harmless no-op if the member wasn't timed out, but
            # necessary since the DB doesn't record which mechanism (role vs. native
            # timeout) was used for the active mute.
            await self.member.timeout(None, reason=self._shorten_reason(reason))

        # record() deactivates the active mute row itself (see _OPPOSITE_ACTION).
        return await self.record(ModerationAction.UNMUTE, reason, comment, moderator_id)

    async def execute_punishment(
        self, punishment: Punishment, reason: str, comment: Optional[str] = None
    ) -> None:
        bot_member = self.guild.me
        moderator_id = bot_member.id if bot_member else None

        if punishment.action is PunishmentAction.STRIKE:
            await self.strike(reason, comment, moderator_id, amount=punishment.count or 1)
        elif punishment.action is PunishmentAction.MUTE:
            await self.mute(reason, comment, moderator_id, duration=punishment.duration)
        elif punishment.action is PunishmentAction.SOFTBAN:
            await self.softban(reason, comment, moderator_id)
        elif punishment.action is PunishmentAction.BAN:
            await self.ban(reason, comment, moderator_id, duration=punishment.duration)
        elif punishment.action is PunishmentAction.KICK:
            await self.kick(reason, comment, moderator_id)


async def is_moderateable(
    guild: discord.Guild, guild_settings: "GuildSettings", target: ModerationTarget
) -> bool:
    """Can the bot itself moderate this target? True unconditionally if the target
    isn't a guild member (e.g. banning a user who already left). Otherwise requires
    the bot's top role to outrank the target's, and the target to not hold a
    protected role."""
    if not isinstance(target, discord.Member):
        return True
    bot_member = guild.me
    if bot_member is None or bot_member.top_role <= target.top_role:
        return False
    protected_role_ids = await guild_settings.get_protected_role_ids(guild.id)
    if protected_role_ids and any(role.id in protected_role_ids for role in target.roles):
        return False
    return True


def is_moderateable_by(moderator: discord.Member, target: ModerationTarget) -> bool:
    """Can `moderator` moderate this target? True unconditionally if the target isn't a
    guild member. Note this rejects self-targeting: comparing a member's own top role
    to itself is never strictly greater, matching modbot's exact (non-obvious) behavior."""
    if not isinstance(target, discord.Member):
        return True
    return moderator.top_role > target.top_role
