"""Multiple-choice questions from Open Trivia DB. url3986 encoding avoids the HTML
entities its default encoding puts in questions."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import unquote

import aiohttp

from .fun_apis import get_json

TRIVIA_URL = "https://opentdb.com/api.php?amount=1&type=multiple&encode=url3986"
TRIVIA_REWARDS = {"easy": 50, "medium": 100, "hard": 150}


@dataclass(frozen=True)
class Question:
    category: str
    difficulty: str
    text: str
    correct: str
    answers: list[str]


def parse_question(data: Any, rng: random.Random) -> Optional[Question]:
    if not isinstance(data, dict) or data.get("response_code") != 0 or not data.get("results"):
        return None
    raw = data["results"][0]
    try:
        correct = unquote(raw["correct_answer"])
        answers = [correct, *(unquote(a) for a in raw["incorrect_answers"])]
        difficulty = raw["difficulty"]
        question = Question(unquote(raw["category"]), difficulty, unquote(raw["question"]), correct, answers)
    except (KeyError, TypeError):
        return None
    if difficulty not in TRIVIA_REWARDS:
        return None
    rng.shuffle(question.answers)
    return question


async def fetch_question(session: aiohttp.ClientSession, rng: random.Random) -> Optional[Question]:
    return parse_question(await get_json(session, TRIVIA_URL), rng)
