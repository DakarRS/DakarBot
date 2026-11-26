import pytest

from dakarbot.social.actions import ACTIONS, tally
from dakarbot.social.counts import ActionCounts

ALICE, BOB, CAROL = 10, 20, 30


async def test_record_counts_up_per_pair_and_action(db):
    counts = ActionCounts(db)
    assert await counts.record("hug", ALICE, BOB) == 1
    assert await counts.record("hug", ALICE, BOB) == 2
    assert await counts.record("hug", BOB, ALICE) == 1
    assert await counts.record("pat", ALICE, BOB) == 1
    assert await counts.record("hug", ALICE, CAROL) == 1


async def test_record_rejects_unknown_action(db):
    with pytest.raises(ValueError):
        await ActionCounts(db).record("punch", ALICE, BOB)


async def test_stats_totals_given_and_received(db):
    counts = ActionCounts(db)
    for _ in range(3):
        await counts.record("hug", ALICE, BOB)
    await counts.record("hug", ALICE, CAROL)
    await counts.record("pat", ALICE, BOB)
    await counts.record("bonk", BOB, ALICE)
    await counts.record("bonk", CAROL, ALICE)

    stats = await counts.stats(ALICE)
    assert stats.given == [("hug", 4), ("pat", 1)]
    assert stats.received == [("bonk", 2)]
    assert (stats.total_given, stats.total_received) == (5, 2)


async def test_stats_for_unknown_user_are_empty(db):
    stats = await ActionCounts(db).stats(ALICE)
    assert (stats.given, stats.received, stats.total_given, stats.total_received) == ([], [], 0, 0)


@pytest.mark.parametrize("name, count, text", [
    ("hug", 1, "Alice has hugged Bob once"),
    ("hug", 2, "Alice has hugged Bob twice"),
    ("hug", 14, "Alice has hugged Bob 14 times"),
    ("bellyrub", 3, "Alice has given Bob bellyrubs 3 times"),
    ("howl", 1, "Alice has howled at Bob once"),
    ("blush", 5, "Bob has made Alice blush 5 times"),
    ("wag", 2, "Bob has made Alice wag their tail twice"),
])
def test_tally_text(name, count, text):
    assert tally(ACTIONS[name], "Alice", "Bob", count) == text


def test_every_action_has_tally_text():
    for action in ACTIONS.values():
        assert "{n}" in action.tally


def test_tally_escapes_markdown():
    assert tally(ACTIONS["hug"], "a*b", "c_d", 1) == r"a\*b has hugged c\_d once"
