from tests.test_telegram_flow import Harness


async def test_invalid_customer_input_stays_on_step_and_review_edit_changes_lead(store):
    h = Harness(store)
    await h.message(1, "/start")
    did = (await store.active_draft(1))["id"]
    await h.callback(1, f"c:service:{did}:electrical")
    await h.message(1, "   ")
    assert (await store.active_draft(1))["step"] == "address"
    await h.message(1, "Первый адрес")
    await h.message(1, "Завтра")
    await h.message(1, "Анна")
    await h.message(1, "123")
    assert (await store.active_draft(1))["step"] == "phone"
    await h.message(1, "+7 (999) 123-45-67")
    await h.callback(1, f"c:skip:{did}")
    await h.callback(1, f"c:edit:{did}:address")
    await h.message(1, "Исправленный адрес")
    assert (await store.active_draft(1))["step"] == "review"
    await h.callback(1, f"c:confirm:{did}")
    lead = await store.lead(1)
    assert lead["address"] == "Исправленный адрес"
    assert lead["phone"] == "+79991234567"
