from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import aiohttp

from ..db import Database
from .actions import ACTIONS

URL_CHECK_TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass(frozen=True)
class Gif:
    id: int
    action: str
    url: str


class GifStore:
    def __init__(self, db: Database):
        self._db = db

    async def seed_if_empty(self, seed: dict[str, list[str]]) -> int:
        """Fills the table on first run only, so GIFs removed later stay removed."""
        if await self._db.fetchone("SELECT 1 FROM gifs LIMIT 1"):
            return 0
        added = 0
        for action, urls in seed.items():
            for url in urls:
                added += await self._db.execute(
                    "INSERT INTO gifs (action, url) VALUES ($1, $2) ON CONFLICT DO NOTHING", action, url
                )
        return added

    async def random_url(self, action: str) -> Optional[str]:
        row = await self._db.fetchone(
            "SELECT url FROM gifs WHERE action = $1 ORDER BY random() LIMIT 1", action
        )
        return row["url"] if row else None

    async def list(self, action: str) -> list[Gif]:
        rows = await self._db.fetchall("SELECT * FROM gifs WHERE action = $1 ORDER BY id", action)
        return [Gif(**row) for row in rows]

    async def add(self, action: str, url: str) -> Optional[Gif]:
        """Returns None if that action already has this URL."""
        if action not in ACTIONS:
            raise ValueError(f"Unknown action: {action}")
        row = await self._db.fetchone(
            "INSERT INTO gifs (action, url) VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING *", action, url
        )
        return Gif(**row) if row else None

    async def remove(self, gif_id: int) -> bool:
        return await self._db.execute("DELETE FROM gifs WHERE id = $1", gif_id) > 0


async def is_media_url(session: aiohttp.ClientSession, url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    try:
        async with session.get(url, timeout=URL_CHECK_TIMEOUT, allow_redirects=True) as response:
            content_type = response.headers.get("Content-Type", "")
            return response.status == 200 and content_type.startswith(("image/", "video/"))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False
