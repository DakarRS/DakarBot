import random

import pytest

from dakarbot.economy.balances import (
    Balances,
    InsufficientFunds,
    PaymentError,
    WorkOutcome,
    WorkResult,
    roll_work,
    validate_payment,
)

GUILD, OTHER_GUILD = 1, 2
ALICE, BOB = 10, 20


async def test_balance_defaults_to_zero(db):
    assert await Balances(db).get(GUILD, ALICE) == 0


async def test_set_balance_is_per_guild(db):
    balances = Balances(db)
    await balances.set(GUILD, ALICE, 500)
    assert await balances.get(GUILD, ALICE) == 500
    assert await balances.get(OTHER_GUILD, ALICE) == 0


async def test_set_balance_rejects_negative(db):
    with pytest.raises(ValueError):
        await Balances(db).set(GUILD, ALICE, -1)


async def test_reset_only_affects_current_guild(db):
    balances = Balances(db)
    await balances.set(GUILD, ALICE, 500)
    await balances.set(OTHER_GUILD, ALICE, 700)
    await balances.reset(GUILD, ALICE)
    assert await balances.get(GUILD, ALICE) == 0
    assert await balances.get(OTHER_GUILD, ALICE) == 700


def test_roll_work_covers_all_outcomes_within_ranges():
    rng = random.Random(1234)
    seen = {}
    for _ in range(600):
        outcome = roll_work(rng)
        seen.setdefault(outcome.result, []).append(outcome.amount)
    assert set(seen) == set(WorkResult)
    assert all(100 <= a <= 500 for a in seen[WorkResult.EARNED])
    assert all(100 <= a <= 200 for a in seen[WorkResult.LOST])
    assert all(a == 0 for a in seen[WorkResult.NOTHING])


async def test_work_earning_adds_to_balance(db):
    balances = Balances(db)
    change, new = await balances.apply_work(GUILD, ALICE, WorkOutcome(WorkResult.EARNED, 300))
    assert (change, new) == (300, 300)


async def test_work_loss_never_goes_below_zero(db):
    balances = Balances(db)
    await balances.set(GUILD, ALICE, 50)
    change, new = await balances.apply_work(GUILD, ALICE, WorkOutcome(WorkResult.LOST, 150))
    assert (change, new) == (-50, 0)
    assert await balances.get(GUILD, ALICE) == 0


async def test_work_loss_with_no_balance_stays_zero(db):
    change, new = await Balances(db).apply_work(GUILD, ALICE, WorkOutcome(WorkResult.LOST, 150))
    assert (change, new) == (0, 0)


@pytest.mark.parametrize(
    "payer, payee, payee_is_bot, amount",
    [
        (ALICE, BOB, False, 0),
        (ALICE, BOB, False, -5),
        (ALICE, ALICE, False, 10),
        (ALICE, BOB, True, 10),
    ],
)
def test_validate_payment_rejects_bad_payments(payer, payee, payee_is_bot, amount):
    with pytest.raises(PaymentError):
        validate_payment(payer, payee, payee_is_bot, amount)


def test_validate_payment_accepts_normal_payment():
    validate_payment(ALICE, BOB, False, 10)


async def test_transfer_moves_money(db):
    balances = Balances(db)
    await balances.set(GUILD, ALICE, 100)
    assert await balances.transfer(GUILD, ALICE, BOB, 40) == (60, 40)
    assert await balances.get(GUILD, ALICE) == 60
    assert await balances.get(GUILD, BOB) == 40


async def test_transfer_with_insufficient_funds_changes_nothing(db):
    balances = Balances(db)
    await balances.set(GUILD, ALICE, 30)
    with pytest.raises(InsufficientFunds):
        await balances.transfer(GUILD, ALICE, BOB, 40)
    assert await balances.get(GUILD, ALICE) == 30
    assert await balances.get(GUILD, BOB) == 0


async def test_transfer_can_spend_entire_balance(db):
    balances = Balances(db)
    await balances.set(GUILD, ALICE, 40)
    assert await balances.transfer(GUILD, ALICE, BOB, 40) == (0, 40)
