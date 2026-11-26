import asyncio
import random

import pytest

from dakarbot.economy.balances import MAX_BALANCE, Balances
from dakarbot.games.rob import (
    FAIL_PERCENT,
    MIN_FINE,
    ROB_COOLDOWN_SECONDS,
    ROB_MIN_ROBBER,
    ROB_MIN_TARGET,
    STEAL_PERCENT,
    RobCooldown,
    RobError,
    Robberies,
    RobRoll,
    roll_rob,
)
from dakarbot.guild_settings import GuildSettings

GUILD = 1
ROBBER, TARGET = 10, 20
T0 = 1_000_000


async def _balances(db, robber: int, target: int) -> None:
    await Balances(db).set(GUILD, ROBBER, robber)
    await Balances(db).set(GUILD, TARGET, target)


def test_roll_rob_percent_ranges():
    rng = random.Random(5)
    rolls = [roll_rob(rng) for _ in range(500)]
    assert {r.success for r in rolls} == {True, False}
    assert all(STEAL_PERCENT[0] <= r.percent <= STEAL_PERCENT[1] for r in rolls if r.success)
    assert all(FAIL_PERCENT[0] <= r.percent <= FAIL_PERCENT[1] for r in rolls if not r.success)


async def test_success_steals_a_share_of_the_target(db):
    await _balances(db, 500, 1000)
    result = await Robberies(db).attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 20))
    assert (result.success, result.amount, result.robber_balance, result.target_balance) == (True, 200, 700, 800)


async def test_failure_fines_robber_and_pays_target(db):
    await _balances(db, 1000, 500)
    result = await Robberies(db).attempt(GUILD, ROBBER, TARGET, T0, RobRoll(False, 15))
    assert (result.success, result.amount, result.robber_balance, result.target_balance) == (False, 150, 850, 650)


async def test_fine_has_a_minimum(db):
    await _balances(db, ROB_MIN_ROBBER, 500)
    result = await Robberies(db).attempt(GUILD, ROBBER, TARGET, T0, RobRoll(False, 10))
    assert result.amount == MIN_FINE


async def test_poor_target_is_refused_without_using_cooldown(db):
    robberies = Robberies(db)
    await _balances(db, 500, ROB_MIN_TARGET - 1)
    with pytest.raises(RobError):
        await robberies.attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 20))
    await Balances(db).set(GUILD, TARGET, ROB_MIN_TARGET)
    await robberies.attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 20))


async def test_poor_robber_is_refused(db):
    await _balances(db, ROB_MIN_ROBBER - 1, 1000)
    with pytest.raises(RobError):
        await Robberies(db).attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 20))


async def test_cannot_rob_yourself(db):
    await Balances(db).set(GUILD, ROBBER, 1000)
    with pytest.raises(RobError):
        await Robberies(db).attempt(GUILD, ROBBER, ROBBER, T0, RobRoll(True, 20))


async def test_cooldown_applies_after_any_attempt(db):
    robberies = Robberies(db)
    await _balances(db, 1000, 1000)
    await robberies.attempt(GUILD, ROBBER, TARGET, T0, RobRoll(False, 10))
    with pytest.raises(RobCooldown) as caught:
        await robberies.attempt(GUILD, ROBBER, TARGET, T0 + ROB_COOLDOWN_SECONDS - 1, RobRoll(True, 20))
    assert caught.value.retry_at == T0 + ROB_COOLDOWN_SECONDS
    await robberies.attempt(GUILD, ROBBER, TARGET, T0 + ROB_COOLDOWN_SECONDS, RobRoll(True, 20))


async def test_concurrent_attempts_only_run_once(db):
    robberies = Robberies(db)
    await _balances(db, 1000, 1000)
    results = await asyncio.gather(
        *(robberies.attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 10)) for _ in range(5)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert await Balances(db).get(GUILD, TARGET) == 900


async def test_opposite_robberies_at_once_dont_deadlock(db):
    robberies = Robberies(db)
    await _balances(db, 1000, 1000)
    await asyncio.gather(
        robberies.attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 10)),
        robberies.attempt(GUILD, TARGET, ROBBER, T0, RobRoll(True, 10)),
    )
    total = await Balances(db).get(GUILD, ROBBER) + await Balances(db).get(GUILD, TARGET)
    assert total == 2000


async def test_theft_is_capped_at_robbers_max_balance(db):
    await _balances(db, MAX_BALANCE - 50, 1000)
    result = await Robberies(db).attempt(GUILD, ROBBER, TARGET, T0, RobRoll(True, 30))
    assert (result.amount, result.target_balance) == (50, 950)


async def test_rob_is_off_by_default(db):
    settings = GuildSettings(db)
    assert await settings.get_rob_enabled(GUILD) is False
    await settings.set_rob_enabled(GUILD, True)
    assert await settings.get_rob_enabled(GUILD) is True
