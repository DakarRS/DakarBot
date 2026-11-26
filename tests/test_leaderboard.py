from datetime import date, timedelta

from dakarbot.economy.balances import Balances
from dakarbot.economy.daily import Daily
from dakarbot.cogs.leaderboard import render
from dakarbot.economy.leaderboard import Entry, Ranking, levels, richest, streaks
from dakarbot.leveling.levels import Levels

GUILD, OTHER_GUILD = 1, 2
DAY = date(2026, 11, 26)


async def _set_balances(db, guild, amounts: dict[int, int]) -> None:
    for user_id, amount in amounts.items():
        await Balances(db).set(guild, user_id, amount)


async def test_richest_orders_by_balance(db):
    await _set_balances(db, GUILD, {1: 100, 2: 300, 3: 200})
    ranking = await richest(db, GUILD, caller_id=2)
    assert ranking.top == [Entry(1, 2, 300), Entry(2, 3, 200), Entry(3, 1, 100)]
    assert ranking.caller == Entry(1, 2, 300)


async def test_ties_share_a_rank(db):
    await _set_balances(db, GUILD, {1: 100, 2: 300, 3: 300})
    ranking = await richest(db, GUILD, caller_id=1)
    assert [(e.rank, e.value) for e in ranking.top] == [(1, 300), (1, 300), (3, 100)]


async def test_caller_outside_top_gets_their_rank(db):
    await _set_balances(db, GUILD, {user_id: 1000 - user_id for user_id in range(1, 16)})
    ranking = await richest(db, GUILD, caller_id=15, limit=10)
    assert len(ranking.top) == 10
    assert ranking.caller == Entry(15, 15, 985)


async def test_caller_with_nothing_has_no_rank(db):
    await _set_balances(db, GUILD, {1: 100})
    ranking = await richest(db, GUILD, caller_id=99)
    assert ranking.caller is None


async def test_zero_balances_are_left_out(db):
    await _set_balances(db, GUILD, {1: 100, 2: 0})
    assert [e.user_id for e in (await richest(db, GUILD, caller_id=1)).top] == [1]


async def test_richest_only_counts_this_guild(db):
    await _set_balances(db, GUILD, {1: 100})
    await _set_balances(db, OTHER_GUILD, {2: 900})
    assert [e.user_id for e in (await richest(db, GUILD, caller_id=1)).top] == [1]


async def test_streaks_rank_active_streaks_only(db):
    daily = Daily(db)
    for offset in range(3):
        await daily.claim(GUILD, 1, DAY + timedelta(days=offset))
    await daily.claim(GUILD, 2, DAY + timedelta(days=2))
    # User 3 last claimed four days before "today", so their streak has ended.
    for offset in range(5):
        await daily.claim(GUILD, 3, DAY - timedelta(days=5 - offset))
    await daily.claim(OTHER_GUILD, 4, DAY + timedelta(days=2))

    ranking = await streaks(db, GUILD, caller_id=3, today=DAY + timedelta(days=3))
    assert ranking.top == [Entry(1, 1, 3), Entry(2, 2, 1)]
    assert ranking.caller is None


def _unit(value: int) -> str:
    return f"{value:,} coins"


def test_render_marks_caller_in_top():
    ranking = Ranking([Entry(1, 5, 300), Entry(2, 6, 100)], Entry(2, 6, 100))
    assert render("Richest", ranking, _unit, caller_id=6) == (
        "## Richest\n**#1** <@5> - 300 coins\n**#2** <@6> - 100 coins (you)"
    )


def test_render_appends_caller_outside_top():
    ranking = Ranking([Entry(1, 5, 300)], Entry(12, 6, 10))
    assert render("Richest", ranking, _unit, caller_id=6).endswith("\n\n**#12** <@6> - 10 coins (you)")


def test_render_empty_board():
    assert render("Richest", Ranking([], None), _unit, caller_id=6) == "## Richest\nNobody's on the board yet."


async def test_levels_rank_by_xp_in_this_guild(db):
    store = Levels(db)
    await store.award(GUILD, 1, 50, 0)
    await store.award(GUILD, 2, 300, 0)
    await store.award(OTHER_GUILD, 3, 900, 0)
    ranking = await levels(db, GUILD, caller_id=1)
    assert ranking.top == [Entry(1, 2, 300), Entry(2, 1, 50)]
