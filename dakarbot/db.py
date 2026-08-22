"""Async PostgreSQL access for DakarBot, backed by an asyncpg connection pool."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import asyncpg

from . import settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_ROWCOUNT_RE = re.compile(r"(\d+)$")


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def connect(cls) -> "Database":
        pool = await asyncpg.create_pool(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
        )
        db = cls(pool)
        await db._init_schema()
        return db

    async def _init_schema(self) -> None:
        statements = [s.strip() for s in SCHEMA_PATH.read_text().split(";") if s.strip()]
        async with self._pool.acquire() as conn:
            for statement in statements:
                await conn.execute(statement)

    async def close(self) -> None:
        await self._pool.close()

    async def execute(self, query: str, *args: Any) -> int:
        """Run a mutating query; returns the affected row count."""
        async with self._pool.acquire() as conn:
            status = await conn.execute(query, *args)
        match = _ROWCOUNT_RE.search(status)
        return int(match.group(1)) if match else 0

    async def insert(self, query: str, *args: Any) -> int:
        """Run an INSERT whose query text ends with `RETURNING id`; returns that id."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def fetchone(self, query: str, *args: Any) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
        return dict(row) if row else None

    async def fetchall(self, query: str, *args: Any) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]
