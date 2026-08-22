"""Bad-word and auto-response domain: guild-scoped, DB-backed chat-triggered filters
(modbot's BadWord.js/AutoResponse.js, sharing its ChatTriggeredFeature.js base and
Trigger.js hierarchy). Both apply either to every channel in a guild ("global") or to
an explicit list of channels.

Deliberately not porting modbot's 4-class Trigger hierarchy (Trigger/IncludeTrigger/
MatchTrigger/RegexTrigger/PhishingTrigger) as a Python class hierarchy: each subclass
only ever overrode `test()`/`asContentString()`, so one dataclass plus a single
`trigger_matches()` dispatch function covers the same behavior with far less
ceremony. `toRegex()` was grepped in modbot and found unused anywhere, so it's
dropped entirely rather than ported.

Cloud Vision image-text scanning (modbot's `enableVision` flag on both features) is
deliberately not ported: it's gated behind a Cloud Vision API integration that
doesn't exist in this port yet (see cogs/settings.py's own scope note on safe-search
for the same reasoning). Add an `enable_vision` column later if that integration
gets built, rather than carrying a dead flag now.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..db import Database
from ..moderation.models import Punishment, PunishmentAction

# Matches modbot's ChatTriggeredFeature 10-minute in-memory cache eviction.
CACHE_TTL = 10 * 60

DEFAULT_BAD_WORD_RESPONSE = "Your message includes words/phrases that are not allowed here!"


# ---- Trigger ----


class TriggerType(str, Enum):
    INCLUDE = "include"
    MATCH = "match"
    REGEX = "regex"
    PHISHING = "phishing"


@dataclass
class Trigger:
    type: TriggerType
    content: str
    flags: Optional[str] = None

    def as_content_string(self) -> str:
        if self.type is TriggerType.REGEX:
            return f"/{self.content}/{self.flags or ''}"
        return self.content

    def as_string(self) -> str:
        return f"{self.type.value}: `{self.as_content_string()}`"

    def supports_images(self) -> bool:
        # Matches modbot: only include/regex triggers ever ran against Cloud Vision
        # OCR text (match/phishing never did) — kept for parity even though vision
        # scanning itself isn't wired up in this port.
        return self.type in (TriggerType.INCLUDE, TriggerType.REGEX)


_JS_REGEX_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def _compile_regex_flags(flags: Optional[str]) -> int:
    result = 0
    for ch in flags or "":
        result |= _JS_REGEX_FLAG_MAP.get(ch, 0)
    return result


def compare_two_strings(a: str, b: str) -> float:
    """Dice's coefficient over character bigrams, ported bit-for-bit from the npm
    `string-similarity` package (not Python's difflib, which uses a different
    algorithm) so the existing 0.85/0.5 similarity thresholds behave the same as
    modbot's."""
    a = re.sub(r"\s+", "", a)
    b = re.sub(r"\s+", "", b)
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0

    bigrams_a = Counter(a[i : i + 2] for i in range(len(a) - 1))
    intersection = 0
    for i in range(len(b) - 1):
        bigram = b[i : i + 2]
        if bigrams_a.get(bigram, 0) > 0:
            bigrams_a[bigram] -= 1
            intersection += 1
    return (2.0 * intersection) / (len(a) + len(b) - 2)


_PHISHING_DOMAIN_RE = re.compile(r"^([^/]+)\.([^./(]+)(?:\(([^)]+)\))?$")
_PHISHING_CONTENT_RE = re.compile(r"https?://([^/]+)\.([^./]+)\b", re.IGNORECASE)


def _matches_phishing(trigger_content: str, content: str) -> bool:
    domain, _, similarity_str = trigger_content.partition(":")
    try:
        similarity = float(similarity_str) if similarity_str else 0.5
    except ValueError:
        similarity = 0.5
    domain = domain.lower()

    match = _PHISHING_DOMAIN_RE.match(domain)
    if not match or not match.group(1) or not match.group(2):
        return False
    expected_domain = match.group(1)
    expected_extensions = (
        [match.group(2), *(e.strip() for e in match.group(3).lower().split(","))]
        if match.group(3)
        else [match.group(2)]
    )

    for found in _PHISHING_CONTENT_RE.finditer(content):
        found_domain = found.group(1).lower()
        found_extension = found.group(2).lower()
        main_part_matches = found_domain == expected_domain or found_domain.endswith(f".{expected_domain}")

        if main_part_matches and found_extension in expected_extensions:
            continue  # the actual domain or a subdomain of it -> not phishing
        if main_part_matches and found_extension not in expected_extensions:
            return True  # right name, wrong TLD -> probably phishing
        if compare_two_strings(expected_domain, found_domain) >= similarity:
            return True  # close enough to the real domain -> probably phishing
    return False


def trigger_matches(trigger: Trigger, content: str) -> bool:
    if trigger.type is TriggerType.INCLUDE:
        return trigger.content.lower() in content.lower()
    if trigger.type is TriggerType.MATCH:
        return trigger.content.lower() == content.lower()
    if trigger.type is TriggerType.REGEX:
        try:
            return re.search(trigger.content, content, _compile_regex_flags(trigger.flags)) is not None
        except re.error:
            return False
    return _matches_phishing(trigger.content, content)


def parse_trigger(trigger_type: str, value: str) -> tuple[Optional[Trigger], Optional[str]]:
    """Returns (trigger, None) on success, or (None, error_message) on failure —
    matches ChatTriggeredFeature.getTrigger's validation, including regex triggers'
    `/pattern/flags` syntax."""
    try:
        type_ = TriggerType(trigger_type)
    except ValueError:
        return None, f"Invalid trigger type {trigger_type}"
    if not value:
        return None, "Empty triggers are not allowed"

    content, flags = value, None
    if type_ is TriggerType.REGEX:
        parts = re.split(r"(?<!\\)/", value)
        if len(parts) < 2 or parts[0]:
            return None, "Invalid regex trigger"
        content, flags = parts[1], (parts[2] if len(parts) > 2 else None)
        try:
            re.compile(content, _compile_regex_flags(flags))
        except re.error:
            return None, "Invalid regex trigger"

    return Trigger(type=type_, content=content, flags=flags), None


@dataclass
class _CacheEntry:
    items: list[Any]
    expires_at: float


class _GuildCache:
    """One per-guild list cache per feature table — mirrors ChatTriggeredFeature's
    10-minute TTL, but as a single guild-wide list rather than JS's separate
    channel-cache/guild-cache split: with Postgres arrays there's no per-channel query
    to bother caching separately, so channel filtering just happens in memory here."""

    def __init__(self) -> None:
        self._cache: dict[int, _CacheEntry] = {}

    def get(self, guild_id: int) -> Optional[list[Any]]:
        entry = self._cache.get(guild_id)
        if entry is None or entry.expires_at <= time.monotonic():
            return None
        return entry.items

    def set(self, guild_id: int, items: list[Any]) -> None:
        self._cache[guild_id] = _CacheEntry(items=items, expires_at=time.monotonic() + CACHE_TTL)

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)


# ---- BadWord ----

_BAD_WORD_COLUMNS = (
    "id, guild_id, trigger_type, trigger_content, trigger_flags, punishment_action,"
    " punishment_duration, punishment_count, response, dm, is_global, channel_ids, priority"
)

_bad_word_cache = _GuildCache()


@dataclass
class BadWord:
    id: Optional[int]
    guild_id: int
    trigger: Trigger
    punishment: Punishment
    response: str
    dm: Optional[str]
    is_global: bool
    channel_ids: list[int]
    priority: int

    @classmethod
    def _from_row(cls, row: dict[str, Any]) -> "BadWord":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            trigger=Trigger(TriggerType(row["trigger_type"]), row["trigger_content"], row["trigger_flags"]),
            punishment=Punishment(
                action=PunishmentAction(row["punishment_action"]),
                duration=row["punishment_duration"],
                count=row["punishment_count"],
            ),
            response=row["response"],
            dm=row["dm"],
            is_global=row["is_global"],
            channel_ids=list(row["channel_ids"]),
            priority=row["priority"],
        )

    def get_response(self) -> Optional[str]:
        if self.response == "disabled":
            return None
        return DEFAULT_BAD_WORD_RESPONSE if self.response == "default" else self.response

    def matches(self, content: str) -> bool:
        return trigger_matches(self.trigger, content)

    def applies_to_channel(self, channel_id: int) -> bool:
        return self.is_global or channel_id in self.channel_ids


async def create_bad_word(db: Database, bad_word: BadWord) -> BadWord:
    new_id = await db.insert(
        f"""
        INSERT INTO bad_words
            (guild_id, trigger_type, trigger_content, trigger_flags, punishment_action,
             punishment_duration, punishment_count, response, dm, is_global, channel_ids, priority)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING id
        """,
        bad_word.guild_id,
        bad_word.trigger.type.value,
        bad_word.trigger.content,
        bad_word.trigger.flags,
        bad_word.punishment.action.value,
        bad_word.punishment.duration,
        bad_word.punishment.count,
        bad_word.response,
        bad_word.dm,
        bad_word.is_global,
        bad_word.channel_ids,
        bad_word.priority,
    )
    bad_word.id = new_id
    _bad_word_cache.invalidate(bad_word.guild_id)
    return bad_word


async def update_bad_word(db: Database, bad_word: BadWord) -> None:
    await db.execute(
        """
        UPDATE bad_words SET
            trigger_type = $1, trigger_content = $2, trigger_flags = $3, punishment_action = $4,
            punishment_duration = $5, punishment_count = $6, response = $7, dm = $8,
            is_global = $9, channel_ids = $10, priority = $11
        WHERE id = $12
        """,
        bad_word.trigger.type.value,
        bad_word.trigger.content,
        bad_word.trigger.flags,
        bad_word.punishment.action.value,
        bad_word.punishment.duration,
        bad_word.punishment.count,
        bad_word.response,
        bad_word.dm,
        bad_word.is_global,
        bad_word.channel_ids,
        bad_word.priority,
        bad_word.id,
    )
    _bad_word_cache.invalidate(bad_word.guild_id)


async def get_bad_word(db: Database, guild_id: int, bad_word_id: int) -> Optional[BadWord]:
    row = await db.fetchone(
        f"SELECT {_BAD_WORD_COLUMNS} FROM bad_words WHERE guild_id = $1 AND id = $2", guild_id, bad_word_id
    )
    return BadWord._from_row(row) if row else None


async def delete_bad_word(db: Database, guild_id: int, bad_word_id: int) -> None:
    await db.execute("DELETE FROM bad_words WHERE guild_id = $1 AND id = $2", guild_id, bad_word_id)
    _bad_word_cache.invalidate(guild_id)


async def get_all_bad_words(db: Database, guild_id: int) -> list[BadWord]:
    cached = _bad_word_cache.get(guild_id)
    if cached is not None:
        return cached
    rows = await db.fetchall(
        f"SELECT {_BAD_WORD_COLUMNS} FROM bad_words WHERE guild_id = $1 ORDER BY id", guild_id
    )
    items = [BadWord._from_row(row) for row in rows]
    _bad_word_cache.set(guild_id, items)
    return items


async def get_bad_words_for_channel(db: Database, guild_id: int, channel_id: int) -> list[BadWord]:
    """Global bad-words plus ones scoped to this channel, highest priority first —
    matches AutoModManager's `#badWords` sort order."""
    all_words = await get_all_bad_words(db, guild_id)
    applicable = [w for w in all_words if w.applies_to_channel(channel_id)]
    applicable.sort(key=lambda w: w.priority, reverse=True)
    return applicable


# ---- AutoResponse ----

_AUTO_RESPONSE_COLUMNS = "id, guild_id, trigger_type, trigger_content, trigger_flags, response, is_global, channel_ids"

_auto_response_cache = _GuildCache()


@dataclass
class AutoResponse:
    id: Optional[int]
    guild_id: int
    trigger: Trigger
    response: str
    is_global: bool
    channel_ids: list[int]

    @classmethod
    def _from_row(cls, row: dict[str, Any]) -> "AutoResponse":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            trigger=Trigger(TriggerType(row["trigger_type"]), row["trigger_content"], row["trigger_flags"]),
            response=row["response"],
            is_global=row["is_global"],
            channel_ids=list(row["channel_ids"]),
        )

    def matches(self, content: str) -> bool:
        return trigger_matches(self.trigger, content)

    def applies_to_channel(self, channel_id: int) -> bool:
        return self.is_global or channel_id in self.channel_ids


async def create_auto_response(db: Database, response: AutoResponse) -> AutoResponse:
    new_id = await db.insert(
        """
        INSERT INTO auto_responses (guild_id, trigger_type, trigger_content, trigger_flags, response, is_global, channel_ids)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        response.guild_id,
        response.trigger.type.value,
        response.trigger.content,
        response.trigger.flags,
        response.response,
        response.is_global,
        response.channel_ids,
    )
    response.id = new_id
    _auto_response_cache.invalidate(response.guild_id)
    return response


async def update_auto_response(db: Database, response: AutoResponse) -> None:
    await db.execute(
        """
        UPDATE auto_responses SET
            trigger_type = $1, trigger_content = $2, trigger_flags = $3, response = $4,
            is_global = $5, channel_ids = $6
        WHERE id = $7
        """,
        response.trigger.type.value,
        response.trigger.content,
        response.trigger.flags,
        response.response,
        response.is_global,
        response.channel_ids,
        response.id,
    )
    _auto_response_cache.invalidate(response.guild_id)


async def get_auto_response(db: Database, guild_id: int, response_id: int) -> Optional[AutoResponse]:
    row = await db.fetchone(
        f"SELECT {_AUTO_RESPONSE_COLUMNS} FROM auto_responses WHERE guild_id = $1 AND id = $2",
        guild_id,
        response_id,
    )
    return AutoResponse._from_row(row) if row else None


async def delete_auto_response(db: Database, guild_id: int, response_id: int) -> None:
    await db.execute("DELETE FROM auto_responses WHERE guild_id = $1 AND id = $2", guild_id, response_id)
    _auto_response_cache.invalidate(guild_id)


async def get_all_auto_responses(db: Database, guild_id: int) -> list[AutoResponse]:
    cached = _auto_response_cache.get(guild_id)
    if cached is not None:
        return cached
    rows = await db.fetchall(
        f"SELECT {_AUTO_RESPONSE_COLUMNS} FROM auto_responses WHERE guild_id = $1 ORDER BY id", guild_id
    )
    items = [AutoResponse._from_row(row) for row in rows]
    _auto_response_cache.set(guild_id, items)
    return items


async def get_auto_responses_for_channel(db: Database, guild_id: int, channel_id: int) -> list[AutoResponse]:
    all_responses = await get_all_auto_responses(db, guild_id)
    return [r for r in all_responses if r.applies_to_channel(channel_id)]
