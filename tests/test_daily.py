import asyncio
from datetime import date, timedelta

import pytest

from dakarbot.economy.balances import MAX_BALANCE, Balances
from dakarbot.economy.daily import AlreadyClaimed, Daily, daily_reward

GUILD, OTHER_GUILD = 1, 2
ALICE = 10
DAY = date(2026, 11, 26)


@pytest.mark.parametrize("streak, reward", [(1, 200), (2, 250), (7, 500), (8, 500), (100, 500)])
def test_reward_grows_with_streak_and_caps_at_seven_days(streak, reward):
    assert daily_reward(streak) == reward


async def test_first_claim_starts_streak_and_pays(db):
    claim = await Daily(db).claim(GUILD, ALICE, DAY)
    assert (claim.streak, claim.reward, claim.balance) == (1, 200, 200)
    assert await Balances(db).get(GUILD, ALICE) == 200


async def test_claim_next_day_extends_streak(db):
    daily = Daily(db)
    await daily.claim(GUILD, ALICE, DAY)
    claim = await daily.claim(GUILD, ALICE, DAY + timedelta(days=1))
    assert (claim.streak, claim.reward, claim.balance) == (2, 250, 450)


async def test_skipping_a_day_resets_streak(db):
    daily = Daily(db)
    await daily.claim(GUILD, ALICE, DAY)
    await daily.claim(GUILD, ALICE, DAY + timedelta(days=1))
    claim = await daily.claim(GUILD, ALICE, DAY + timedelta(days=3))
    assert (claim.streak, claim.reward) == (1, 200)


async def test_second_claim_same_day_is_refused_and_pays_nothing(db):
    daily = Daily(db)
    await daily.claim(GUILD, ALICE, DAY)
    with pytest.raises(AlreadyClaimed):
        await daily.claim(GUILD, ALICE, DAY)
    assert await Balances(db).get(GUILD, ALICE) == 200


async def test_streak_keeps_counting_past_the_reward_cap(db):
    daily = Daily(db)
    for offset in range(9):
        claim = await daily.claim(GUILD, ALICE, DAY + timedelta(days=offset))
    assert (claim.streak, claim.reward) == (9, 500)


async def test_streaks_are_per_guild(db):
    daily = Daily(db)
    await daily.claim(GUILD, ALICE, DAY)
    claim = await daily.claim(OTHER_GUILD, ALICE, DAY)
    assert claim.streak == 1


async def test_payout_is_capped_at_max_balance(db):
    await Balances(db).set(GUILD, ALICE, MAX_BALANCE - 50)
    claim = await Daily(db).claim(GUILD, ALICE, DAY)
    assert (claim.reward, claim.balance) == (50, MAX_BALANCE)


async def test_concurrent_claims_only_pay_once(db):
    daily = Daily(db)
    results = await asyncio.gather(
        *(daily.claim(GUILD, ALICE, DAY) for _ in range(5)), return_exceptions=True
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, AlreadyClaimed) for r in results if isinstance(r, Exception))
    assert await Balances(db).get(GUILD, ALICE) == 200
