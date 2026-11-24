from dakarbot.suggestions import SuggestionStatus, Suggestions, decision_custom_id, parse_decision_custom_id


async def test_create_and_get(db):
    store = Suggestions(db)
    created = await store.create(user_id=10, guild_id=1, content="Add a /coinflip")
    fetched = await store.get(created.id)
    assert fetched.content == "Add a /coinflip"
    assert fetched.status is SuggestionStatus.PENDING


async def test_suggestion_can_only_be_decided_once(db):
    store = Suggestions(db)
    s = await store.create(user_id=10, guild_id=1, content="idea")
    approved = await store.decide(s.id, SuggestionStatus.APPROVED, decided_by=99, reason="Nice")
    assert approved.status is SuggestionStatus.APPROVED
    assert approved.decision_reason == "Nice"
    assert await store.decide(s.id, SuggestionStatus.DENIED, decided_by=99, reason=None) is None
    assert (await store.get(s.id)).status is SuggestionStatus.APPROVED


async def test_message_location_is_stored(db):
    store = Suggestions(db)
    s = await store.create(user_id=10, guild_id=None, content="idea")
    await store.set_message(s.id, channel_id=5, message_id=6)
    fetched = await store.get(s.id)
    assert (fetched.channel_id, fetched.message_id) == (5, 6)


def test_custom_id_round_trip():
    custom_id = decision_custom_id(SuggestionStatus.DENIED, 42)
    assert parse_decision_custom_id(custom_id) == (SuggestionStatus.DENIED, 42)


def test_parse_ignores_other_components():
    assert parse_decision_custom_id("something-else") is None
    assert parse_decision_custom_id("suggestion:approved:notanumber") is None
    assert parse_decision_custom_id("suggestion:pending:1") is None
