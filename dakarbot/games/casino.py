"""Coinflip and slots odds. The slots table pays back about 97% over time."""
from __future__ import annotations

import random
from fractions import Fraction

SIDES = ("heads", "tails")
SYMBOL_WEIGHTS = {"Cherry": 6, "Lemon": 5, "Bell": 4, "Bar": 3, "Seven": 2}
THREE_OF_A_KIND = {"Cherry": 5, "Lemon": 10, "Bell": 20, "Bar": 40, "Seven": 100}
TWO_CHERRIES = Fraction(3, 2)


def flip(rng: random.Random) -> str:
    return rng.choice(SIDES)


def coinflip_multiplier(call: str, result: str) -> int:
    return 2 if call == result else 0


def spin(rng: random.Random) -> tuple[str, str, str]:
    a, b, c = rng.choices(list(SYMBOL_WEIGHTS), weights=list(SYMBOL_WEIGHTS.values()), k=3)
    return a, b, c


def slots_multiplier(reels: tuple[str, str, str]) -> int | Fraction:
    if reels[0] == reels[1] == reels[2]:
        return THREE_OF_A_KIND[reels[0]]
    if reels.count("Cherry") == 2:
        return TWO_CHERRIES
    return 0
