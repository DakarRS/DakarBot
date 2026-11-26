import asyncio
from fractions import Fraction

import pytest

from dakarbot.economy.balances import MAX_BALANCE, Balances, InsufficientFunds
from dakarbot.games.wagers import MIN_BET, BetError, Wagers, validate_bet

GUILD = 1
ALICE = 10


@pytest.mark.parametrize("bet", [0, MIN_BET - 1, MAX_BALANCE + 1])
def test_validate_bet_rejects_out_of_range(bet):
    with pytest.raises(BetError):
        validate_bet(bet)


def test_validate_bet_accepts_minimum():
    validate_bet(MIN_BET)


async def test_play_win_pays_multiplier(db):
    await Balances(db).set(GUILD, ALICE, 100)
    played = await Wagers(db).play(GUILD, ALICE, 50, 2)
    assert (played.payout, played.balance) == (100, 150)


async def test_play_loss_keeps_the_bet(db):
    await Balances(db).set(GUILD, ALICE, 100)
    played = await Wagers(db).play(GUILD, ALICE, 50, 0)
    assert (played.payout, played.balance) == (0, 50)


async def test_play_rounds_fractional_payouts_down(db):
    await Balances(db).set(GUILD, ALICE, 100)
    played = await Wagers(db).play(GUILD, ALICE, 15, Fraction(3, 2))
    assert (played.payout, played.balance) == (22, 107)


async def test_play_without_funds_changes_nothing(db):
    await Balances(db).set(GUILD, ALICE, 40)
    with pytest.raises(InsufficientFunds):
        await Wagers(db).play(GUILD, ALICE, 50, 2)
    assert await Balances(db).get(GUILD, ALICE) == 40


async def test_hold_takes_bet_and_settle_pays_once(db):
    wagers = Wagers(db)
    await Balances(db).set(GUILD, ALICE, 100)
    hold = await wagers.hold(GUILD, ALICE, 60, "blackjack")
    assert await Balances(db).get(GUILD, ALICE) == 40
    assert await wagers.settle(hold, 120) == 160
    assert await wagers.settle(hold, 120) is None
    assert await Balances(db).get(GUILD, ALICE) == 160


async def test_concurrent_settles_only_pay_once(db):
    wagers = Wagers(db)
    await Balances(db).set(GUILD, ALICE, 100)
    hold = await wagers.hold(GUILD, ALICE, 100, "blackjack")
    results = await asyncio.gather(*(wagers.settle(hold, 200) for _ in range(5)))
    assert sum(r is not None for r in results) == 1
    assert await Balances(db).get(GUILD, ALICE) == 200


async def test_raise_hold_takes_more(db):
    wagers = Wagers(db)
    await Balances(db).set(GUILD, ALICE, 100)
    hold = await wagers.hold(GUILD, ALICE, 40, "blackjack")
    assert await wagers.raise_hold(hold, 40) == 20
    with pytest.raises(InsufficientFunds):
        await wagers.raise_hold(hold, 40)
    assert await wagers.refund_all() == 1
    assert await Balances(db).get(GUILD, ALICE) == 100


async def test_hold_without_funds_fails(db):
    with pytest.raises(InsufficientFunds):
        await Wagers(db).hold(GUILD, ALICE, 50, "rps")


async def test_refund_all_returns_open_holds(db):
    wagers = Wagers(db)
    await Balances(db).set(GUILD, ALICE, 100)
    settled = await wagers.hold(GUILD, ALICE, 30, "rps")
    await wagers.hold(GUILD, ALICE, 50, "rps")
    await wagers.settle(settled, 0)
    assert await wagers.refund_all() == 1
    assert await Balances(db).get(GUILD, ALICE) == 70
    assert await wagers.refund_all() == 0
