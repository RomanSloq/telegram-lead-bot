import sqlite3

import pytest

from leadbot.domain import normalize_phone, validate_field
from tests.conftest import filled_draft


async def test_complete_questionnaire_creates_lead_and_all_deliveries(store):
    did = await filled_draft(store)
    lead_id, created = await store.confirm(did, 1, frozenset({9, 10}))
    assert created and lead_id == 1
    assert (await store.lead(lead_id))["phone"] == "+79991234567"
    assert len(await store.due_deliveries(1000)) == 3


async def test_required_field_does_not_advance_on_invalid_input(store):
    did = await store.create_draft(1, 1)
    assert validate_field("address", "   ") is None
    assert (await store.draft(did, 1))["step"] == "service"
    assert await store.save_field(did, 1, "address", "x") is None


def test_phone_normalization_and_rejection():
    assert normalize_phone("+7 (999) 123-45-67") == "+79991234567"
    assert normalize_phone("999 123 45 67") == "9991234567"
    assert normalize_phone("+123") is None
    assert normalize_phone("1234567890123456") is None
    assert normalize_phone("123abc4567890") is None


async def test_comment_can_be_skipped(store):
    did = await filled_draft(store)
    assert (await store.draft(did, 1))["comment"] == ""
    assert (await store.draft(did, 1))["step"] == "review"


async def test_back_and_edit_preserve_new_value(store):
    did = await filled_draft(store)
    await store.set_step(did, 1, "address", editing=True)
    assert await store.save_field(did, 1, "address", "Новый адрес") == "review"
    lead_id, _ = await store.confirm(did, 1, frozenset({9}))
    assert (await store.lead(lead_id))["address"] == "Новый адрес"


async def test_cancel_does_not_delete_existing_lead_and_start_replaces_draft(store):
    did = await filled_draft(store)
    lead_id, _ = await store.confirm(did, 1, frozenset({9}))
    active = await store.create_draft(1, 1)
    assert active != did
    assert await store.create_draft(1, 1) == active
    assert await store.cancel_draft(1)
    assert await store.active_draft(1) is None
    assert await store.lead(lead_id)


async def test_two_independent_leads_from_one_customer(store):
    first = await filled_draft(store)
    first_id, _ = await store.confirm(first, 1, frozenset({9}))
    second = await filled_draft(store)
    second_id, _ = await store.confirm(second, 1, frozenset({9}))
    assert first != second and first_id != second_id


async def test_repeated_confirmation_is_idempotent_even_after_restart(store):
    did = await filled_draft(store)
    first = await store.confirm(did, 1, frozenset({9}))
    second = await store.confirm(did, 1, frozenset({9}))
    assert first == (second[0], True)
    assert second == (first[0], False)
    assert len(await store.due_deliveries(1000)) == 2
    from leadbot.db import Store

    reopened = Store(store.path, clock=lambda: 1000)
    assert await reopened.confirm(did, 1, frozenset({9})) == (first[0], False)


async def test_database_error_rolls_back_lead_and_deliveries(store):
    did = await filled_draft(store)
    async with store.connect() as db:
        await db.executescript(
            "CREATE TRIGGER reject_delivery BEFORE INSERT ON deliveries "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
        )
        await db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        await store.confirm(did, 1, frozenset({9}))
    assert await store.leads() == []
    assert (await store.draft(did, 1))["status"] == "active"


async def test_status_and_stale_action(store):
    did = await filled_draft(store)
    lid, _ = await store.confirm(did, 1, frozenset({9}))
    assert await store.change_status(lid, "new", "in_progress")
    assert not await store.change_status(lid, "new", "done")
    assert not await store.change_status(lid, "in_progress", "bogus")
    assert (await store.lead(lid))["status"] == "in_progress"


async def test_note_replacement_and_empty_note_rejected(store):
    did = await filled_draft(store)
    lid, _ = await store.confirm(did, 1, frozenset({9}))
    assert await store.save_note(lid, "Первое")
    assert not await store.save_note(lid, " ")
    assert await store.save_note(lid, "Исправлено")
    assert (await store.lead(lid))["note"] == "Исправлено"


async def test_draft_and_saved_data_survive_reopening_database(store):
    did = await store.create_draft(1, 1)
    await store.save_field(did, 1, "service", "repair")
    from leadbot.db import Store

    reopened = Store(store.path, clock=lambda: 1000)
    assert (await reopened.active_draft(1))["service"] == "repair"
    other = await filled_draft(store, 2)
    lid, _ = await store.confirm(other, 2, frozenset({9}))
    await store.change_status(lid, "new", "done")
    await store.save_note(lid, "Готово")
    assert (await reopened.lead(lid))["status"] == "done"
    assert (await reopened.lead(lid))["note"] == "Готово"
