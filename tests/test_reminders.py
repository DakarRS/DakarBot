import asyncio

import pytest

from dakarbot.reminders import MAX_REMINDERS, MIN_SECONDS, ReminderError, Reminders

ALICE, BOB = 10, 20
CHANNEL = 99
T0 = 1_000_000


async def test_add_and_list_in_due_order(db):
    reminders = Reminders(db)
    later = await reminders.add(ALICE, CHANNEL, "later", T0, 3600)
    sooner = await reminders.add(ALICE, None, "sooner", T0, 600)
    assert (later.due, later.channel_id, later.text) == (T0 + 3600, CHANNEL, "later")
    assert [r.id for r in await reminders.list(ALICE)] == [sooner.id, later.id]
    assert await reminders.list(BOB) == []


async def test_reminder_must_be_at_least_a_minute_away(db):
    with pytest.raises(ReminderError):
        await Reminders(db).add(ALICE, None, "too soon", T0, MIN_SECONDS - 1)


async def test_text_is_required(db):
    with pytest.raises(ReminderError):
        await Reminders(db).add(ALICE, None, "   ", T0, 600)


async def test_each_user_has_a_limit(db):
    reminders = Reminders(db)
    for n in range(MAX_REMINDERS):
        await reminders.add(ALICE, None, f"r{n}", T0, 600)
    with pytest.raises(ReminderError):
        await reminders.add(ALICE, None, "one more", T0, 600)
    await reminders.add(BOB, None, "bob's", T0, 600)


async def test_cancel_only_your_own(db):
    reminders = Reminders(db)
    reminder = await reminders.add(ALICE, None, "mine", T0, 600)
    assert await reminders.cancel(BOB, reminder.id) is False
    assert await reminders.cancel(ALICE, reminder.id) is True
    assert await reminders.cancel(ALICE, reminder.id) is False


async def test_take_due_returns_and_removes_due_reminders(db):
    reminders = Reminders(db)
    due = await reminders.add(ALICE, None, "due", T0, 600)
    await reminders.add(ALICE, None, "not yet", T0, 3600)
    taken = await reminders.take_due(T0 + 600)
    assert [r.id for r in taken] == [due.id]
    assert await reminders.take_due(T0 + 600) == []
    assert [r.text for r in await reminders.list(ALICE)] == ["not yet"]


async def test_overdue_reminders_are_caught_up(db):
    reminders = Reminders(db)
    await reminders.add(ALICE, None, "a", T0, 600)
    await reminders.add(BOB, None, "b", T0, 1200)
    assert len(await reminders.take_due(T0 + 99_999)) == 2


async def test_concurrent_takes_deliver_once(db):
    reminders = Reminders(db)
    await reminders.add(ALICE, None, "once", T0, 600)
    results = await asyncio.gather(*(reminders.take_due(T0 + 600) for _ in range(3)))
    assert sum(len(r) for r in results) == 1
