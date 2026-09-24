from leadbot.delivery import DeliveryWorker
from tests.test_telegram_flow import Harness, submit


async def test_back_restart_cancel_and_restore_customer_draft(store):
    h = Harness(store)
    await h.message(1, "/start")
    did = (await store.active_draft(1))["id"]
    await h.callback(1, f"c:service:{did}:plumbing")
    await h.callback(1, f"c:back:{did}")
    assert (await store.active_draft(1))["step"] == "service"
    await h.callback(1, f"c:service:{did}:repair")
    await h.message(1, "Адрес 2")
    h.reset_routers()  # The customer draft lives in SQLite.
    await h.message(1, "/start")
    await h.callback(1, f"c:resume:{did}")
    assert (await store.active_draft(1))["step"] == "preferred_time"
    await h.callback(1, f"c:restart:{did}")
    new_id = (await store.active_draft(1))["id"]
    assert new_id != did and (await store.draft(did, 1))["status"] == "cancelled"
    await h.message(1, "/cancel")
    assert await store.active_draft(1) is None


async def test_manager_missing_lead_stale_status_and_note_cancel(store):
    h = Harness(store)
    await submit(h)
    await h.message(9, "/lead 999")
    assert any("не найдена" in text for text in h.texts())
    await h.callback(9, "m:status:1:new:in_progress")
    await h.callback(9, "m:status:1:new:done")
    assert (await store.lead(1))["status"] == "in_progress"
    await h.callback(9, "m:note:1")
    await h.message(9, "/cancel")
    await h.message(9, "Этот текст не должен стать заметкой")
    assert (await store.lead(1))["note"] == ""


async def test_failure_commands_show_and_requeue_delivery(store):
    h = Harness(store)
    await submit(h)

    async def fail(*args, **kwargs):
        from aiogram.exceptions import TelegramForbiddenError
        from aiogram.methods import SendMessage

        raise TelegramForbiddenError(method=SendMessage(chat_id=9, text="test"), message="blocked")

    await DeliveryWorker(store, fail, clock=lambda: 1000).process_due()
    failures = await store.failed_deliveries()
    assert failures
    await h.message(9, "/delivery_failures")
    assert any("Доставка #" in text for text in h.texts())
    await h.message(9, f"/retry_delivery {failures[0]['id']}")
    assert (await store.delivery(failures[0]["id"]))["state"] == "pending"
