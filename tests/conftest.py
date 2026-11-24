"""Shared fixtures. Database tests run against a throwaway `dakarbot_test` database on
the compose Postgres, so the real SQL (constraints, transactions, read-only mode) is
what gets exercised. HTTP is always faked."""
from __future__ import annotations

import asyncpg
import pytest

from dakarbot import settings
from dakarbot.db import Database

TEST_DB_NAME = "dakarbot_test"


async def _ensure_test_database() -> None:
    conn = await asyncpg.connect(
        host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
        password=settings.DB_PASSWORD, database="postgres",
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def _session_db():
    await _ensure_test_database()
    db = await Database.connect(database=TEST_DB_NAME)
    yield db
    await db.close()


@pytest.fixture
async def db(_session_db: Database) -> Database:
    tables = await _session_db.fetchall(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    names = ", ".join(f'"{t["tablename"]}"' for t in tables)
    if names:
        await _session_db.execute(f"TRUNCATE {names} RESTART IDENTITY")
    return _session_db


class FakeResponse:
    def __init__(self, status: int = 200, json_data=None, content_type: str = "application/json"):
        self.status = status
        self._json = json_data
        self.headers = {"Content-Type": content_type}
        self.content_type = content_type

    async def json(self, content_type=None):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp.ClientSession: maps URL -> FakeResponse (or an exception
    to raise) and records every URL requested."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        result = self.routes.get(url, FakeResponse(status=404))
        if isinstance(result, Exception):
            raise result
        return result

    head = get


@pytest.fixture
def fake_session():
    return FakeSession
