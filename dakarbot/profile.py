"""Everything /profile shows, gathered from the economy, leveling and action stores."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .db import Database
from .economy.balances import Balances
from .economy.leaderboard import levels, richest
from .economy.shop import Shop
from .leveling.curve import level_for_xp
from .social.counts import ActionCounts


@dataclass(frozen=True)
class Profile:
    balance: int
    balance_rank: Optional[int]
    xp: int
    level: int
    level_rank: Optional[int]
    streak: int
    top_action: Optional[tuple[str, int]]
    items: int


async def _active_streak(db: Database, guild_id: int, user_id: int, today: date) -> int:
    row = await db.fetchone(
        "SELECT streak FROM daily_claims WHERE guild_id = $1 AND user_id = $2 AND last_claim >= $3",
        guild_id, user_id, today - timedelta(days=1),
    )
    return row["streak"] if row else 0


async def load_profile(db: Database, guild_id: int, user_id: int, today: date) -> Profile:
    money = await richest(db, guild_id, user_id, limit=1)
    xp_rank = await levels(db, guild_id, user_id, limit=1)
    xp = xp_rank.caller.value if xp_rank.caller else 0
    stats = await ActionCounts(db).stats(user_id)
    inventory = await Shop(db).inventory(guild_id, user_id)
    return Profile(
        balance=await Balances(db).get(guild_id, user_id),
        balance_rank=money.caller.rank if money.caller else None,
        xp=xp,
        level=level_for_xp(xp),
        level_rank=xp_rank.caller.rank if xp_rank.caller else None,
        streak=await _active_streak(db, guild_id, user_id, today),
        top_action=stats.given[0] if stats.given else None,
        items=sum(entry.count for entry in inventory),
    )
