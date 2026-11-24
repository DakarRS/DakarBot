import asyncpg
import pytest

from dakarbot.blacklist import Blacklist, BlacklistKind
from dakarbot.currency import Balances
from dakarbot.readonly_sql import QueryRejected, check_query, format_result, run_readonly


async def test_blacklist_add_remove_and_cache(db):
    blacklist = Blacklist(db)
    await blacklist.load()
    assert not blacklist.is_blacklisted(BlacklistKind.USER, 5)

    assert await blacklist.add(BlacklistKind.USER, 5, "spam") is True
    assert await blacklist.add(BlacklistKind.USER, 5, "again") is False
    assert blacklist.is_blacklisted(BlacklistKind.USER, 5)
    assert not blacklist.is_blacklisted(BlacklistKind.SERVER, 5)  # kinds are separate

    fresh = Blacklist(db)
    await fresh.load()
    assert fresh.is_blacklisted(BlacklistKind.USER, 5)
    assert [(e.kind, e.target_id, e.reason) for e in await fresh.list()] == [(BlacklistKind.USER, 5, "spam")]

    assert await blacklist.remove(BlacklistKind.USER, 5) is True
    assert await blacklist.remove(BlacklistKind.USER, 5) is False
    assert not blacklist.is_blacklisted(BlacklistKind.USER, 5)


@pytest.mark.parametrize("query", [
    "SELECT 1", "  select * from balances;", "WITH x AS (SELECT 1) SELECT * FROM x",
    "EXPLAIN SELECT 1", "SHOW server_version",
])
def test_check_query_allows_reads(query):
    assert check_query(query)


@pytest.mark.parametrize("query", [
    "DELETE FROM balances", "DROP TABLE balances", "UPDATE balances SET amount = 0",
    "SELECT 1; DROP TABLE balances", "", "   ",
])
def test_check_query_rejects_writes_and_multiple_statements(query):
    with pytest.raises(QueryRejected):
        check_query(query)


async def test_run_readonly_returns_rows(db):
    await Balances(db).set(1, 10, 250)
    result = await run_readonly(db, "SELECT guild_id, user_id, amount FROM balances")
    assert result.columns == ["guild_id", "user_id", "amount"]
    assert result.rows == [[1, 10, 250]]
    assert result.truncated is False


async def test_run_readonly_blocks_writes_hidden_in_cte(db):
    await Balances(db).set(1, 10, 250)
    with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        await run_readonly(db, "WITH gone AS (DELETE FROM balances RETURNING *) SELECT * FROM gone")
    assert await Balances(db).get(1, 10) == 250


async def test_run_readonly_truncates_rows(db):
    result = await run_readonly(db, "SELECT generate_series(1, 100) AS n", row_limit=10)
    assert len(result.rows) == 10
    assert result.truncated is True


async def test_run_readonly_times_out(db):
    with pytest.raises(asyncpg.QueryCanceledError):
        await run_readonly(db, "SELECT pg_sleep(2)", timeout_ms=100)


def test_format_result_fits_a_message():
    from dakarbot.readonly_sql import QueryResult

    result = QueryResult(columns=["n"], rows=[["x" * 50] for _ in range(200)], truncated=True)
    text = format_result(result)
    assert len(text) <= 1900
    assert text.startswith("```")
