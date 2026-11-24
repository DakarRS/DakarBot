"""Input rules for /poll, matching Discord's native poll limits."""
from __future__ import annotations

MIN_ANSWERS = 2
MAX_ANSWERS = 10
ANSWER_MAX_LENGTH = 55
QUESTION_MAX_LENGTH = 300

DURATION_CHOICES_HOURS = {"1 hour": 1, "4 hours": 4, "8 hours": 8, "1 day": 24, "3 days": 72, "1 week": 168}


class PollInputError(ValueError):
    """Raised with a message that's safe to show the user."""


def parse_answers(text: str) -> list[str]:
    answers: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        answer = line.strip()
        if not answer or answer.casefold() in seen:
            continue
        if len(answer) > ANSWER_MAX_LENGTH:
            raise PollInputError(f"Answers can be at most {ANSWER_MAX_LENGTH} characters: \"{answer[:20]}…\"")
        seen.add(answer.casefold())
        answers.append(answer)
    if not MIN_ANSWERS <= len(answers) <= MAX_ANSWERS:
        raise PollInputError(f"A poll needs {MIN_ANSWERS} to {MAX_ANSWERS} different answers, one per line.")
    return answers
