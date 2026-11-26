import random

import pytest

from dakarbot.economy.balances import MAX_BALANCE, Balances
from dakarbot.trivia import TRIVIA_REWARDS, TRIVIA_URL, fetch_question, parse_question
from tests.conftest import FakeResponse, FakeSession

GUILD, ALICE = 1, 10

API_RESULT = {
    "response_code": 0,
    "results": [{
        "type": "multiple",
        "difficulty": "medium",
        "category": "Science%3A%20Computers",
        "question": "What%20does%20%22CPU%22%20stand%20for%3F",
        "correct_answer": "Central%20Processing%20Unit",
        "incorrect_answers": ["Computer%20Personal%20Unit", "Central%20Process%20Unit", "Core%20Power%20Unit"],
    }],
}


def test_parse_decodes_and_mixes_in_the_correct_answer():
    question = parse_question(API_RESULT, random.Random(1))
    assert question.category == "Science: Computers"
    assert question.text == 'What does "CPU" stand for?'
    assert question.correct == "Central Processing Unit"
    assert question.difficulty == "medium"
    assert sorted(question.answers) == sorted([
        "Central Processing Unit", "Computer Personal Unit", "Central Process Unit", "Core Power Unit",
    ])


def test_parse_shuffles_answer_order():
    orders = {tuple(parse_question(API_RESULT, random.Random(seed)).answers) for seed in range(20)}
    assert len(orders) > 1


@pytest.mark.parametrize("data", [
    None, {}, {"response_code": 5, "results": []}, {"response_code": 0, "results": []},
    {"response_code": 0, "results": [{"question": "x"}]},
])
def test_parse_rejects_bad_responses(data):
    assert parse_question(data, random.Random(1)) is None


async def test_fetch_question_uses_the_api():
    session = FakeSession({TRIVIA_URL: FakeResponse(json_data=API_RESULT)})
    question = await fetch_question(session, random.Random(1))
    assert question.correct == "Central Processing Unit"
    assert session.requested == [TRIVIA_URL]


async def test_fetch_question_outage_returns_none():
    session = FakeSession({TRIVIA_URL: FakeResponse(status=503)})
    assert await fetch_question(session, random.Random(1)) is None


def test_rewards_grow_with_difficulty():
    assert TRIVIA_REWARDS == {"easy": 50, "medium": 100, "hard": 150}


async def test_balance_add_is_capped(db):
    balances = Balances(db)
    assert await balances.add(GUILD, ALICE, 100) == (100, 100)
    await balances.set(GUILD, ALICE, MAX_BALANCE - 10)
    assert await balances.add(GUILD, ALICE, 100) == (10, MAX_BALANCE)
