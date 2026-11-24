"""Small public-API lookups shared by the fun commands."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger("dakarbot.fun")

FOX_URL = "https://randomfox.ca/floof/"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def get_json(session: aiohttp.ClientSession, url: str) -> Optional[Any]:
    """None for a missing resource or an outage; callers tell the user, not the error channel."""
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                if response.status != 404:
                    logger.info("GET %s returned %s", url, response.status)
                return None
            return await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
        logger.info("GET %s failed: %s", url, error)
        return None


async def fetch_fox(session: aiohttp.ClientSession) -> Optional[str]:
    data = await get_json(session, FOX_URL)
    return data.get("image") if isinstance(data, dict) else None
