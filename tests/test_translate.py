import pytest

from dakarbot.translate import (
    LANGUAGES,
    MAX_CHUNK_BYTES,
    MAX_INPUT_CHARS,
    QuotaExceeded,
    TranslateError,
    build_url,
    locale_to_language,
    split_chunks,
    translate,
)
from tests.conftest import FakeResponse, FakeSession


def _ok(text: str, detected: str = "fr") -> FakeResponse:
    return FakeResponse(json_data={
        "responseData": {"translatedText": text, "match": 0.85, "detectedLanguage": detected},
        "quotaFinished": False, "responseStatus": 200,
    })


def test_build_url_encodes_text_and_pair():
    url = build_url("Bonjour & ça va ?", "en")
    assert url == "https://api.mymemory.translated.net/get?q=Bonjour+%26+%C3%A7a+va+%3F&langpair=autodetect%7Cen"


@pytest.mark.parametrize("locale, language", [
    ("en-US", "en"), ("en-GB", "en"), ("es-419", "es"), ("es-ES", "es"), ("pt-BR", "pt-BR"),
    ("zh-CN", "zh-CN"), ("zh-TW", "zh-TW"), ("sv-SE", "sv"), ("fr", "fr"), ("ja", "ja"), (None, "en"),
    ("xx-YY", "en"),
])
def test_locale_to_language(locale, language):
    assert locale_to_language(locale) == language


def test_every_language_choice_is_named():
    assert 0 < len(LANGUAGES) <= 25
    assert all(name for name in LANGUAGES.values())


def test_short_text_is_one_chunk():
    assert split_chunks("Hello there.") == ["Hello there."]


def test_long_text_splits_on_whitespace_within_byte_limit():
    text = " ".join(["mot"] * 400)
    chunks = split_chunks(text)
    assert len(chunks) > 1
    assert all(len(c.encode()) <= MAX_CHUNK_BYTES for c in chunks)
    assert "".join(chunks) == text


def test_multibyte_text_respects_byte_limit():
    text = "日本語" * 200  # no spaces, 3 bytes per character
    chunks = split_chunks(text)
    assert all(len(c.encode()) <= MAX_CHUNK_BYTES for c in chunks)
    assert "".join(chunks) == text


async def test_translate_returns_text_and_detected_language():
    session = FakeSession({build_url("Bonjour", "en"): _ok("Hello")})
    result = await translate(session, "Bonjour", "en")
    assert (result.text, result.detected, result.truncated) == ("Hello", "fr", False)


async def test_translate_unescapes_html_entities():
    session = FakeSession({build_url("C'est", "en"): _ok("It&#39;s")})
    assert (await translate(session, "C'est", "en")).text == "It's"


async def test_translate_joins_chunks_in_order():
    text = " ".join(f"mot{n}" for n in range(150))  # several chunks, under MAX_INPUT_CHARS
    chunks = split_chunks(text)
    session = FakeSession({build_url(c, "en"): _ok(f"[{i}]") for i, c in enumerate(chunks)})
    result = await translate(session, text, "en")
    assert result.text == " ".join(f"[{i}]" for i in range(len(chunks)))
    assert session.requested == [build_url(c, "en") for c in chunks]


async def test_overlong_input_is_truncated():
    text = "a " * MAX_INPUT_CHARS
    session = FakeSession({build_url(c, "en"): _ok("x") for c in split_chunks(text[:MAX_INPUT_CHARS])})
    result = await translate(session, text, "en")
    assert result.truncated


async def test_api_error_status_is_not_shown_as_a_translation():
    session = FakeSession({build_url("hello", "en"): FakeResponse(json_data={
        "responseData": {"translatedText": "'XX' IS AN INVALID TARGET LANGUAGE"},
        "quotaFinished": None, "responseStatus": "403",
    })})
    with pytest.raises(TranslateError) as caught:
        await translate(session, "hello", "en")
    assert "INVALID" not in str(caught.value)


async def test_quota_finished_raises_quota_exceeded():
    session = FakeSession({build_url("hello", "en"): FakeResponse(json_data={
        "responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS"},
        "quotaFinished": True, "responseStatus": 429,
    })})
    with pytest.raises(QuotaExceeded):
        await translate(session, "hello", "en")


async def test_http_429_raises_quota_exceeded():
    session = FakeSession({build_url("hello", "en"): FakeResponse(status=429)})
    with pytest.raises(QuotaExceeded):
        await translate(session, "hello", "en")


async def test_outage_raises_translate_error():
    session = FakeSession({build_url("hello", "en"): FakeResponse(status=503)})
    with pytest.raises(TranslateError):
        await translate(session, "hello", "en")


async def test_blank_text_is_refused():
    with pytest.raises(TranslateError):
        await translate(FakeSession({}), "   ", "en")


@pytest.mark.parametrize("detected, target, same", [
    ("en-GB", "en", True), ("fr", "en", False), ("zh-CN", "zh-TW", True), (None, "en", False),
])
def test_same_language(detected, target, same):
    from dakarbot.cogs.translate import _same_language
    assert _same_language(detected, target) is same
