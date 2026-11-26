import random
from fractions import Fraction
from itertools import product

import pytest

from dakarbot.games.casino import (
    SYMBOL_WEIGHTS,
    coinflip_multiplier,
    flip,
    slots_multiplier,
    spin,
)
from dakarbot.games.rps import CHOICES, rps_payouts


def test_flip_lands_on_both_sides():
    rng = random.Random(3)
    assert {flip(rng) for _ in range(50)} == {"heads", "tails"}


@pytest.mark.parametrize("call, result, multiplier", [("heads", "heads", 2), ("heads", "tails", 0)])
def test_coinflip_pays_double_on_a_correct_call(call, result, multiplier):
    assert coinflip_multiplier(call, result) == multiplier


def test_spin_gives_three_known_symbols():
    rng = random.Random(3)
    for _ in range(50):
        reels = spin(rng)
        assert len(reels) == 3 and set(reels) <= set(SYMBOL_WEIGHTS)


@pytest.mark.parametrize("reels, multiplier", [
    (("Seven", "Seven", "Seven"), 100),
    (("Cherry", "Cherry", "Cherry"), 5),
    (("Cherry", "Bell", "Cherry"), Fraction(3, 2)),
    (("Bell", "Bell", "Lemon"), 0),
    (("Bar", "Lemon", "Seven"), 0),
])
def test_slots_payout_table(reels, multiplier):
    assert slots_multiplier(reels) == multiplier


def test_slots_keep_a_small_house_edge():
    total = sum(SYMBOL_WEIGHTS.values())
    rtp = sum(
        Fraction(SYMBOL_WEIGHTS[a] * SYMBOL_WEIGHTS[b] * SYMBOL_WEIGHTS[c], total ** 3) * slots_multiplier((a, b, c))
        for a, b, c in product(SYMBOL_WEIGHTS, repeat=3)
    )
    assert Fraction(9, 10) < rtp < 1


@pytest.mark.parametrize("a, b, payouts", [
    ("rock", "scissors", (200, 0)),
    ("scissors", "paper", (200, 0)),
    ("paper", "rock", (200, 0)),
    ("rock", "paper", (0, 200)),
    ("rock", "rock", (100, 100)),
])
def test_rps_payouts(a, b, payouts):
    assert rps_payouts(a, b, 100) == payouts


def test_rps_choices():
    assert set(CHOICES) == {"rock", "paper", "scissors"}
