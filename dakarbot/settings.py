"""DakarBot settings, loaded from environment variables (a .env file is picked up automatically)."""
from __future__ import annotations

import base64
import json
import os

from dotenv import load_dotenv

load_dotenv()


def _split_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part) for part in raw.split(",") if part.strip()]


TOKEN = os.environ.get("DAKARBOT_TOKEN")
OWNER_IDS = _split_ids(os.environ.get("DAKARBOT_OWNER_IDS"))

# When set, slash commands sync only to these guilds instead of globally — guild-scoped
# commands propagate instantly, vs. up to an hour for global commands, so this is meant
# for local/dev testing. Leave unset in production.
DEBUG_GUILD_IDS = _split_ids(os.environ.get("DAKARBOT_DEBUG_GUILD_IDS")) or None

# Errors and warnings are posted here when set.
_error_channel_raw = os.environ.get("DAKARBOT_ERROR_CHANNEL_ID")
ERROR_CHANNEL_ID = int(_error_channel_raw) if _error_channel_raw else None

DB_HOST = os.environ.get("DAKARBOT_DATABASE_HOST", "localhost")
DB_PORT = int(os.environ.get("DAKARBOT_DATABASE_PORT", "5432"))
DB_USER = os.environ.get("DAKARBOT_DATABASE_USER")
DB_PASSWORD = os.environ.get("DAKARBOT_DATABASE_PASSWORD")
DB_NAME = os.environ.get("DAKARBOT_DATABASE_DATABASE")

# GCP-style credentials don't fit a flat KEY=VALUE env var, so they're passed
# as a base64-encoded JSON blob (same approach modbot uses for the same reason).
_google_credentials_raw = os.environ.get("DAKARBOT_GOOGLE_CREDENTIALS")
GOOGLE_CREDENTIALS = (
    json.loads(base64.b64decode(_google_credentials_raw)) if _google_credentials_raw else None
)
GOOGLE_API_KEY = os.environ.get("DAKARBOT_GOOGLE_API_KEY")
