from __future__ import annotations

CHOICES = ("rock", "paper", "scissors")
_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def rps_payouts(a: str, b: str, bet: int) -> tuple[int, int]:
    """What each player gets back from a pot of both bets."""
    if a == b:
        return bet, bet
    return (2 * bet, 0) if _BEATS[a] == b else (0, 2 * bet)
