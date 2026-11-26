"""Translation through MyMemory's free, keyless API. It takes at most 500 bytes per
request and about 5,000 characters a day in total, so long text is split into chunks
and capped at MAX_INPUT_CHARS. Errors come back as normal-looking responses with the
error in translatedText, so responseStatus is always checked first."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger("dakarbot.translate")

API_URL = "https://api.mymemory.translated.net/get"
MAX_CHUNK_BYTES = 500
MAX_INPUT_CHARS = 1500
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

# The /translate choices (Discord allows 25).
LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "ru": "Russian", "uk": "Ukrainian",
    "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "ja": "Japanese", "ko": "Korean",
    "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)", "id": "Indonesian",
    "vi": "Vietnamese", "th": "Thai", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "el": "Greek", "cs": "Czech",
}
# Discord locales outside the choices that MyMemory still handles.
_EXTRA_TARGETS = {"pt-BR", "ro", "hu", "hr", "lt", "bg", "no"}
DEFAULT_LANGUAGE = "en"


class TranslateError(Exception):
    """Raised with a message that's safe to show the user."""


class QuotaExceeded(TranslateError):
    def __init__(self) -> None:
        super().__init__("The free translation limit for today has been used up. Try again tomorrow.")


@dataclass(frozen=True)
class Translation:
    text: str
    detected: Optional[str]
    truncated: bool


def locale_to_language(locale: Optional[str]) -> str:
    """Maps a Discord locale (en-US, es-419, pt-BR...) to a target MyMemory accepts."""
    if not locale:
        return DEFAULT_LANGUAGE
    if locale in LANGUAGES or locale in _EXTRA_TARGETS:
        return locale
    base = locale.split("-")[0]
    return base if base in LANGUAGES or base in _EXTRA_TARGETS else DEFAULT_LANGUAGE


def language_name(code: Optional[str]) -> str:
    if not code:
        return "an unknown language"
    return LANGUAGES.get(code) or LANGUAGES.get(code.split("-")[0]) or code


def build_url(text: str, target: str) -> str:
    return f"{API_URL}?{urlencode({'q': text, 'langpair': f'autodetect|{target}'})}"


def _fits(text: str) -> bool:
    return len(text.encode()) <= MAX_CHUNK_BYTES


def split_chunks(text: str) -> list[str]:
    """Splits on whitespace into pieces of at most MAX_CHUNK_BYTES; joined, they give back `text`."""
    chunks: list[str] = []
    current = ""
    for token in re.findall(r"\S+\s*|\s+", text):
        if _fits(current + token):
            current += token
            continue
        if current:
            chunks.append(current)
            current = ""
        while not _fits(token):
            cut = len(token)
            while not _fits(token[:cut]):
                cut -= 1
            chunks.append(token[:cut])
            token = token[cut:]
        current = token
    if current:
        chunks.append(current)
    return chunks


async def _translate_chunk(session: aiohttp.ClientSession, chunk: str, target: str) -> tuple[str, Optional[str]]:
    url = build_url(chunk, target)
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            if response.status == 429:
                raise QuotaExceeded()
            if response.status != 200:
                logger.info("Translation request returned %s", response.status)
                raise TranslateError("Translation isn't available right now. Try again later.")
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
        logger.info("Translation request failed: %s", error)
        raise TranslateError("Translation isn't available right now. Try again later.") from None
    if not isinstance(data, dict):
        raise TranslateError("Translation isn't available right now. Try again later.")
    if data.get("quotaFinished") or str(data.get("responseStatus")) == "429":
        raise QuotaExceeded()
    result = data.get("responseData") or {}
    if str(data.get("responseStatus")) != "200" or not result.get("translatedText"):
        logger.info("Translation failed: %s", data.get("responseDetails"))
        raise TranslateError("I couldn't translate that.")
    return html.unescape(result["translatedText"]).strip(), result.get("detectedLanguage")


async def translate(session: aiohttp.ClientSession, text: str, target: str) -> Translation:
    text = text.strip()
    if not text:
        raise TranslateError("There's no text to translate.")
    truncated = len(text) > MAX_INPUT_CHARS
    text = text[:MAX_INPUT_CHARS]
    parts, detected = [], None
    for chunk in split_chunks(text):
        translated, chunk_language = await _translate_chunk(session, chunk, target)
        parts.append(translated)
        detected = detected or chunk_language
    return Translation(" ".join(parts), detected, truncated)
