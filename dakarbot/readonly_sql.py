"""/owner sql: read-only queries only. The keyword check is just for a clear error
message; the real guard is the read-only transaction, which Postgres enforces even for
writes hidden inside a CTE."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .db import Database

DEFAULT_ROW_LIMIT = 50
DEFAULT_TIMEOUT_MS = 5000
MESSAGE_BUDGET = 1900
CELL_WIDTH = 40

_ALLOWED_FIRST_WORDS = {"select", "with", "explain", "show", "values", "table"}


class QueryRejected(ValueError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def check_query(query: str) -> str:
    cleaned = query.strip().rstrip(";").strip()
    if not cleaned:
        raise QueryRejected("The query is empty.")
    if ";" in cleaned:
        raise QueryRejected("Only one statement at a time.")
    first_word = re.split(r"\s+", cleaned, maxsplit=1)[0].lower()
    if first_word not in _ALLOWED_FIRST_WORDS:
        raise QueryRejected("Only read queries (SELECT, WITH, EXPLAIN, SHOW, VALUES, TABLE) are allowed.")
    return cleaned


async def run_readonly(
    db: Database, query: str, *, row_limit: int = DEFAULT_ROW_LIMIT, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> QueryResult:
    cleaned = check_query(query)
    async with db.transaction(readonly=True) as conn:
        await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        statement = await conn.prepare(cleaned)
        records = await statement.fetch()
        columns = [attr.name for attr in statement.get_attributes()]
    rows = [list(r.values()) for r in records[:row_limit]]
    return QueryResult(columns=columns, rows=rows, truncated=len(records) > row_limit)


def _cell(value: Any) -> str:
    text = "NULL" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= CELL_WIDTH else text[: CELL_WIDTH - 1] + "…"


def format_result(result: QueryResult) -> str:
    if not result.columns:
        return "Query returned no columns."
    table = [result.columns] + [[_cell(v) for v in row] for row in result.rows]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(result.columns))]

    def line(row: list) -> str:
        return " | ".join(str(v).ljust(w) for v, w in zip(row, widths)).rstrip()

    lines = [line(table[0]), "-+-".join("-" * w for w in widths)]
    shown = 0
    for row in table[1:]:
        candidate = line(row)
        if sum(len(l) + 1 for l in lines) + len(candidate) > MESSAGE_BUDGET - 80:
            break
        lines.append(candidate)
        shown += 1

    footer = f"{shown} row(s) shown"
    if result.truncated or shown < len(result.rows):
        footer += ", more were cut off"
    return "```\n" + "\n".join(lines) + "\n```\n" + footer
