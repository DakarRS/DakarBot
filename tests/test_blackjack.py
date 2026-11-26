import random
from collections import Counter

import pytest

from dakarbot.games.blackjack import Blackjack, GameError, Outcome, hand_value, new_deck


@pytest.mark.parametrize("cards, value", [
    (["A", "K"], 21), (["A", "A", "9"], 21), (["A", "K", "5"], 16), (["K", "Q", "5"], 25), (["A", "6"], 17),
])
def test_hand_value(cards, value):
    assert hand_value(cards) == value


def test_new_deck_is_a_full_shuffled_deck():
    deck = new_deck(random.Random(1))
    assert len(deck) == 52
    assert set(Counter(deck).values()) == {4}


def _game(player, dealer, rest=()):
    # Deal order: player, dealer, player, dealer, then draws.
    return Blackjack([player[0], dealer[0], player[1], dealer[1], *rest])


def test_player_blackjack_pays_three_to_two():
    game = _game(["A", "K"], ["9", "7"])
    assert game.finished and game.outcome is Outcome.BLACKJACK
    assert game.payout(100) == 250


def test_both_blackjack_is_a_push():
    game = _game(["A", "K"], ["A", "Q"])
    assert game.outcome is Outcome.PUSH and game.payout(100) == 100


def test_dealer_blackjack_wins_immediately():
    game = _game(["K", "9"], ["A", "Q"])
    assert game.outcome is Outcome.LOSE and game.payout(100) == 0


def test_hitting_past_21_busts():
    game = _game(["K", "6"], ["9", "7"], ["Q"])
    game.hit()
    assert game.finished and game.outcome is Outcome.LOSE
    assert game.player == ["K", "6", "Q"]


def test_hitting_to_21_stands_automatically():
    game = _game(["K", "6"], ["9", "8"], ["5"])
    game.hit()
    assert game.finished and game.outcome is Outcome.WIN


def test_dealer_draws_to_17():
    game = _game(["K", "Q"], ["9", "5"], ["3"])
    game.stand()
    assert game.dealer == ["9", "5", "3"]
    assert game.outcome is Outcome.WIN and game.payout(100) == 200


def test_dealer_stands_on_soft_17():
    game = _game(["10", "8"], ["A", "6"], ["K"])
    game.stand()
    assert game.dealer == ["A", "6"] and game.outcome is Outcome.WIN


def test_dealer_bust_is_a_win():
    game = _game(["K", "2"], ["K", "6"], ["Q"])
    game.stand()
    assert game.outcome is Outcome.WIN


def test_equal_totals_push():
    game = _game(["K", "8"], ["9", "9"])
    game.stand()
    assert game.outcome is Outcome.PUSH and game.payout(100) == 100


def test_higher_dealer_total_loses():
    game = _game(["K", "7"], ["K", "9"])
    game.stand()
    assert game.outcome is Outcome.LOSE


def test_double_draws_one_card_and_doubles_the_payout():
    game = _game(["6", "5"], ["9", "7"], ["K", "10"])
    game.double()
    assert game.player == ["6", "5", "K"] and game.doubled and game.finished
    assert game.outcome is Outcome.WIN and game.payout(100) == 400


def test_double_only_on_first_two_cards():
    game = _game(["2", "3"], ["9", "7"], ["4", "5"])
    game.hit()
    assert not game.can_double
    with pytest.raises(GameError):
        game.double()


def test_no_moves_after_the_game_ends():
    game = _game(["A", "K"], ["9", "7"])
    for move in (game.hit, game.stand, game.double):
        with pytest.raises(GameError):
            move()
