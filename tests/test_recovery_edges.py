import asyncio

from aiogram.types import Update

from leadbot.delivery import DeliveryWorker
from tests.conftest import filled_draft
from tests.test_telegram_flow import Harness


async def test_worker_sends_due_rows_on_startup(store):
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9}))
    sent = []

    async def send(recipient, text, **kwargs):
        sent.append(recipient)

    worker = DeliveryWorker(store, send, clock=lambda: 1000)
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(100):
            if len(sent) == 2 and not await store.due_deliveries(1000):
                break
            await asyncio.sleep(0.001)
        assert sorted(sent) == [1, 9]
        assert not await store.due_deliveries(1000)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_inaccessible_old_callback_does_not_modify_lead(store):
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9}))
    h = Harness(store)
    raw = {
        "update_id": 1,
        "callback_query": {
            "id": "old",
            "chat_instance": "test",
            "from": {"id": 9, "is_bot": False, "first_name": "Manager"},
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": 9, "type": "private"},
            },
            "data": "m:status:1:new:done",
        },
    }
    await h.dispatcher.feed_update(h.bot, Update.model_validate(raw))
    assert (await store.lead(1))["status"] == "new"
