import asyncio
import random

import pytest

from dakarbot.economy.balances import MAX_BALANCE, Balances
from dakarbot.cogs.leveling import progress_bar
from dakarbot.leveling.curve import XP_RANGE, level_for_xp, level_label, progress, roll_xp, xp_for_next
from dakarbot.leveling.levels import AWARD_COOLDOWN_SECONDS, LevelError, Levels, level_up_payout

GUILD, OTHER_GUILD = 1, 2
ALICE = 10
ROLE_A, ROLE_B = 100, 200
T0 = 1_000_000
LATER = T0 + AWARD_COOLDOWN_SECONDS


@pytest.mark.parametrize("level, needed", [(0, 100), (1, 155), (2, 220), (10, 1100)])
def test_xp_needed_for_next_level(level, needed):
    assert xp_for_next(level) == needed


@pytest.mark.parametrize("xp, level", [(0, 0), (99, 0), (100, 1), (254, 1), (255, 2), (4675, 10)])
def test_level_for_total_xp(xp, level):
    assert level_for_xp(xp) == level


def test_progress_within_level():
    assert progress(300) == (2, 45, 220)


def test_roll_xp_stays_in_range():
    rng = random.Random(7)
    rolls = {roll_xp(rng) for _ in range(500)}
    assert min(rolls) == XP_RANGE[0] and max(rolls) == XP_RANGE[1]


def test_payout_is_level_times_100():
    assert level_up_payout([1, 2, 3]) == 600


async def test_first_award_below_level_one_pays_nothing(db):
    award = await Levels(db).award(GUILD, ALICE, 20, T0)
    assert (award.xp, award.level, award.new_levels, award.payout) == (20, 0, [], 0)
    assert await Balances(db).get(GUILD, ALICE) == 0


async def test_awards_within_cooldown_are_ignored(db):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 20, T0)
    assert await levels.award(GUILD, ALICE, 20, LATER - 1) is None
    assert (await levels.award(GUILD, ALICE, 20, LATER)).xp == 40


async def test_level_up_pays_into_balance(db):
    award = await Levels(db).award(GUILD, ALICE, 100, T0)
    assert (award.level, award.new_levels, award.payout, award.balance) == (1, [1], 100, 100)
    assert await Balances(db).get(GUILD, ALICE) == 100


async def test_big_gain_crosses_several_levels(db):
    award = await Levels(db).award(GUILD, ALICE, 300, T0)
    assert (award.level, award.new_levels, award.payout) == (2, [1, 2], 300)


async def test_levels_are_per_guild(db):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 100, T0)
    award = await levels.award(OTHER_GUILD, ALICE, 20, T0)
    assert (award.xp, award.level) == (20, 0)


async def test_payout_is_capped_at_max_balance(db):
    await Balances(db).set(GUILD, ALICE, MAX_BALANCE - 30)
    award = await Levels(db).award(GUILD, ALICE, 100, T0)
    assert (award.payout, award.balance) == (30, MAX_BALANCE)


async def test_concurrent_awards_only_count_once(db):
    levels = Levels(db)
    results = await asyncio.gather(*(levels.award(GUILD, ALICE, 20, T0) for _ in range(5)))
    assert sum(r is not None for r in results) == 1
    assert (await levels.get(GUILD, ALICE)).xp == 20


async def test_get_unknown_member_is_level_zero(db):
    info = await Levels(db).get(GUILD, ALICE)
    assert (info.xp, info.level) == (0, 0)


async def test_cash_in_trades_xp_for_coins_and_lowers_level(db):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 300, T0)  # level 2, pays 300
    result = await levels.cash_in(GUILD, ALICE, 100)
    assert (result.xp, result.level, result.coins, result.balance) == (200, 1, 100, 400)
    assert (await levels.get(GUILD, ALICE)).xp == 200


@pytest.mark.parametrize("amount", [0, -5, 301])
async def test_cash_in_rejects_bad_amounts(db, amount):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 300, T0)
    with pytest.raises(LevelError):
        await levels.cash_in(GUILD, ALICE, amount)
    assert (await levels.get(GUILD, ALICE)).xp == 300


async def test_releveling_after_cash_in_pays_nothing_again(db):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 300, T0)
    await levels.cash_in(GUILD, ALICE, 100)  # back to level 1
    award = await levels.award(GUILD, ALICE, 100, LATER)  # level 2 again
    assert (award.level, award.new_levels, award.payout) == (2, [], 0)


async def test_cash_in_only_spends_xp_that_fits_under_max_balance(db):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 300, T0)
    await Balances(db).set(GUILD, ALICE, MAX_BALANCE - 50)
    result = await levels.cash_in(GUILD, ALICE, 200)
    assert (result.coins, result.xp, result.balance) == (50, 250, MAX_BALANCE)


async def test_cash_in_at_max_balance_is_refused(db):
    levels = Levels(db)
    await levels.award(GUILD, ALICE, 300, T0)
    await Balances(db).set(GUILD, ALICE, MAX_BALANCE)
    with pytest.raises(LevelError):
        await levels.cash_in(GUILD, ALICE, 100)


async def test_rewards_are_listed_by_level_and_replaceable(db):
    levels = Levels(db)
    await levels.set_reward(GUILD, 10, ROLE_B)
    await levels.set_reward(GUILD, 5, ROLE_A)
    await levels.set_reward(GUILD, 10, ROLE_A)
    assert await levels.rewards(GUILD) == [(5, ROLE_A), (10, ROLE_A)]
    assert await levels.rewards(OTHER_GUILD) == []
    assert await levels.remove_reward(GUILD, 5) is True
    assert await levels.remove_reward(GUILD, 5) is False


async def test_award_reports_roles_for_newly_reached_levels(db):
    levels = Levels(db)
    await levels.set_reward(GUILD, 1, ROLE_A)
    await levels.set_reward(GUILD, 2, ROLE_B)
    await levels.set_reward(GUILD, 3, 300)
    award = await levels.award(GUILD, ALICE, 300, T0)
    assert award.role_ids == [ROLE_A, ROLE_B]


@pytest.mark.parametrize("done, total, bar", [
    (0, 100, "░" * 12), (50, 100, "█" * 6 + "░" * 6), (100, 100, "█" * 12), (99, 100, "█" * 11 + "░"),
])
def test_progress_bar(done, total, bar):
    assert progress_bar(done, total) == bar


def test_level_label():
    assert level_label(300) == "Level 2 (300 XP)"
