from datetime import date, timedelta

from dakarbot.economy.balances import Balances
from dakarbot.economy.daily import Daily
from dakarbot.economy.shop import Shop
from dakarbot.leveling.levels import Levels
from dakarbot.profile import load_profile
from dakarbot.social.counts import ActionCounts

GUILD, OTHER_GUILD = 1, 2
ALICE, BOB = 10, 20
DAY = date(2026, 11, 26)


async def _no_grant(item) -> None:
    pass


async def test_empty_profile(db):
    profile = await load_profile(db, GUILD, ALICE, DAY)
    assert (profile.balance, profile.balance_rank, profile.xp, profile.level, profile.level_rank) == (0, None, 0, 0, None)
    assert (profile.streak, profile.top_action, profile.items) == (0, None, 0)


async def test_profile_gathers_everything(db):
    await Daily(db).claim(GUILD, ALICE, DAY - timedelta(days=1))
    await Daily(db).claim(GUILD, ALICE, DAY)  # streak 2, balance 450
    await Balances(db).set(GUILD, BOB, 1000)
    await Levels(db).award(GUILD, ALICE, 300, 0)  # level 2, pays 300 -> 750
    shop = Shop(db)
    await shop.add(GUILD, "Trophy", 100)
    await shop.buy(GUILD, ALICE, "Trophy", _no_grant)
    await shop.buy(GUILD, ALICE, "Trophy", _no_grant)  # balance 550
    counts = ActionCounts(db)
    await counts.record("hug", ALICE, BOB)
    await counts.record("hug", ALICE, BOB)
    await counts.record("pat", ALICE, BOB)

    profile = await load_profile(db, GUILD, ALICE, DAY)
    assert (profile.balance, profile.balance_rank) == (550, 2)
    assert (profile.xp, profile.level, profile.level_rank) == (300, 2, 1)
    assert profile.streak == 2
    assert profile.top_action == ("hug", 2)
    assert profile.items == 2


async def test_broken_streak_shows_zero(db):
    await Daily(db).claim(GUILD, ALICE, DAY - timedelta(days=2))
    assert (await load_profile(db, GUILD, ALICE, DAY)).streak == 0


async def test_profile_is_per_guild_except_actions(db):
    await Balances(db).set(OTHER_GUILD, ALICE, 500)
    await ActionCounts(db).record("hug", ALICE, BOB)
    profile = await load_profile(db, GUILD, ALICE, DAY)
    assert profile.balance == 0
    assert profile.top_action == ("hug", 1)
