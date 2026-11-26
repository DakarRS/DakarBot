"""Single-deck blackjack. The dealer stands on all 17s, a natural pays 3:2, and
doubling is allowed on the first two cards. Suits don't matter, so cards are ranks."""
from __future__ import annotations

import math
import random
from enum import Enum

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
DEALER_STANDS_ON = 17


class GameError(Exception):
    pass


class Outcome(Enum):
    BLACKJACK = "blackjack"
    WIN = "win"
    PUSH = "push"
    LOSE = "lose"


_MULTIPLIER = {Outcome.BLACKJACK: 2.5, Outcome.WIN: 2, Outcome.PUSH: 1, Outcome.LOSE: 0}


def new_deck(rng: random.Random) -> list[str]:
    deck = list(RANKS) * 4
    rng.shuffle(deck)
    return deck


def hand_value(cards: list[str]) -> int:
    total = sum(11 if c == "A" else 10 if c in ("J", "Q", "K") else int(c) for c in cards)
    aces = cards.count("A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _natural(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


class Blackjack:
    def __init__(self, deck: list[str]):
        self._deck = list(deck)
        self.player = [self._draw()]
        self.dealer = [self._draw()]
        self.player.append(self._draw())
        self.dealer.append(self._draw())
        self.doubled = False
        self.outcome: Outcome | None = None
        if _natural(self.player) or _natural(self.dealer):
            both = _natural(self.player) and _natural(self.dealer)
            self.outcome = Outcome.PUSH if both else Outcome.BLACKJACK if _natural(self.player) else Outcome.LOSE

    @property
    def finished(self) -> bool:
        return self.outcome is not None

    @property
    def can_double(self) -> bool:
        return not self.finished and len(self.player) == 2

    def _draw(self) -> str:
        return self._deck.pop(0)

    def _require_open(self) -> None:
        if self.finished:
            raise GameError("This game is over.")

    def hit(self) -> None:
        self._require_open()
        self.player.append(self._draw())
        value = hand_value(self.player)
        if value > 21:
            self.outcome = Outcome.LOSE
        elif value == 21:
            self.stand()

    def stand(self) -> None:
        self._require_open()
        while hand_value(self.dealer) < DEALER_STANDS_ON:
            self.dealer.append(self._draw())
        player, dealer = hand_value(self.player), hand_value(self.dealer)
        if dealer > 21 or player > dealer:
            self.outcome = Outcome.WIN
        elif player == dealer:
            self.outcome = Outcome.PUSH
        else:
            self.outcome = Outcome.LOSE

    def double(self) -> None:
        if not self.can_double:
            raise GameError("You can only double on your first two cards.")
        self.doubled = True
        self.player.append(self._draw())
        if hand_value(self.player) > 21:
            self.outcome = Outcome.LOSE
        else:
            self.stand()

    def payout(self, bet: int) -> int:
        """What comes back for the original bet (doubling is accounted for here)."""
        if self.outcome is None:
            raise GameError("The game isn't over yet.")
        stake = bet * 2 if self.doubled else bet
        return math.floor(stake * _MULTIPLIER[self.outcome])
