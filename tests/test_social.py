import pytest

from dakarbot.social.actions import ACTIONS, ActionError, describe
from dakarbot.social.gifs import GifStore, is_media_url
from dakarbot.social.seed_gifs import SEED_GIFS
from tests.conftest import FakeResponse

AGREED_ACTIONS = {
    "hug", "bonk", "boop", "kiss", "lick", "bellyrub", "cuddle", "highfive", "rawr", "howl", "pat",
    "blush", "happy", "wag",
}


def test_action_set_matches_agreed_list():
    assert set(ACTIONS) == AGREED_ACTIONS


def test_every_action_has_seed_gifs():
    assert set(SEED_GIFS) == AGREED_ACTIONS
    assert all(SEED_GIFS[name] for name in AGREED_ACTIONS)


def test_targeted_action_text():
    assert describe(ACTIONS["hug"], "Alice", "Bob", None) == "**Alice** hugged **Bob**"


def test_targeted_action_includes_reason():
    text = describe(ACTIONS["pat"], "Alice", "Bob", "good job")
    assert text == "**Alice** patted **Bob**\n**Reason:** good job"


def test_targeted_action_requires_target():
    with pytest.raises(ActionError):
        describe(ACTIONS["hug"], "Alice", None, None)


@pytest.mark.parametrize("name, solo, because", [
    ("blush", "**Alice** blushes", "**Alice** blushes because of **Bob**"),
    ("happy", "**Alice** is happy", "**Alice** is happy because of **Bob**"),
    ("wag", "**Alice** wags their tail", "**Alice** wags their tail because of **Bob**"),
])
def test_feelings_have_optional_target(name, solo, because):
    assert describe(ACTIONS[name], "Alice", None, None) == solo
    assert describe(ACTIONS[name], "Alice", "Bob", None) == because


def test_howl_works_with_and_without_target():
    assert describe(ACTIONS["howl"], "Alice", None, None) == "**Alice** howls. Awoo!"
    assert describe(ACTIONS["howl"], "Alice", "Bob", None) == "**Alice** howled at **Bob**. Awoo!"


def test_names_are_markdown_escaped():
    assert describe(ACTIONS["hug"], "a*b", "c_d", None) == r"**a\*b** hugged **c\_d**"


async def test_seed_only_runs_on_empty_table(db):
    store = GifStore(db)
    assert await store.seed_if_empty({"hug": ["https://x/1.gif", "https://x/2.gif"]}) == 2
    removed = (await store.list("hug"))[0]
    await store.remove(removed.id)
    assert await store.seed_if_empty({"hug": ["https://x/1.gif", "https://x/2.gif"]}) == 0
    assert [g.url for g in await store.list("hug")] == ["https://x/2.gif"]


async def test_add_list_remove(db):
    store = GifStore(db)
    gif = await store.add("hug", "https://x/1.gif")
    assert gif is not None
    assert await store.add("hug", "https://x/1.gif") is None  # duplicate
    assert [g.url for g in await store.list("hug")] == ["https://x/1.gif"]
    assert await store.remove(gif.id) is True
    assert await store.remove(gif.id) is False
    assert await store.list("hug") == []


async def test_add_rejects_unknown_action(db):
    with pytest.raises(ValueError):
        await GifStore(db).add("slap", "https://x/1.gif")


async def test_random_url_picks_from_action(db):
    store = GifStore(db)
    await store.add("hug", "https://x/hug.gif")
    await store.add("pat", "https://x/pat.gif")
    assert await store.random_url("hug") == "https://x/hug.gif"
    assert await store.random_url("bonk") is None


async def test_is_media_url_accepts_images(fake_session):
    session = fake_session({"https://x/a.gif": FakeResponse(content_type="image/gif")})
    assert await is_media_url(session, "https://x/a.gif") is True


async def test_is_media_url_rejects_html_and_errors(fake_session):
    session = fake_session({
        "https://x/page": FakeResponse(content_type="text/html"),
        "https://x/boom": OSError("network down"),
    })
    assert await is_media_url(session, "https://x/page") is False
    assert await is_media_url(session, "https://x/missing.gif") is False
    assert await is_media_url(session, "https://x/boom") is False


async def test_is_media_url_rejects_non_http(fake_session):
    assert await is_media_url(fake_session({}), "ftp://x/a.gif") is False
