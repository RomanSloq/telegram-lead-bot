import asyncio

from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import SendMessage

from leadbot.db import Store
from leadbot.delivery import DeliveryWorker
from tests.conftest import filled_draft


def api_error(cls, *, retry_after=None):
    method = SendMessage(chat_id=9, text="test")
    if retry_after is not None:
        return cls(method=method, message="test", retry_after=retry_after)
    return cls(method=method, message="test")


async def test_delivery_failure_after_commit_keeps_lead(store):
    did = await filled_draft(store)
    lid, _ = await store.confirm(did, 1, frozenset({9}))

    async def send(*args, **kwargs):
        raise api_error(TelegramNetworkError)

    worker = DeliveryWorker(store, send, clock=lambda: 1000)
    await worker.process_due()
    assert await store.lead(lid)
    rows = await store.due_deliveries(1060)
    assert len(rows) == 2 and all(r["state"] == "retry" for r in rows)


async def test_retry_schedule_and_restart_without_real_wait(tmp_path):
    now = [1000]
    store = Store(tmp_path / "retry.sqlite3", clock=lambda: now[0])
    await store.init()
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9}))

    async def send(*args, **kwargs):
        raise api_error(TelegramNetworkError)

    for expected in (1060, 1360, 2260, 5860):
        worker = DeliveryWorker(Store(store.path, clock=lambda: now[0]), send, clock=lambda: now[0])
        await worker.process_due()
        rows = await store.due_deliveries(expected - 1)
        assert not rows
        rows = await store.due_deliveries(expected)
        assert len(rows) == 2
        now[0] = expected
    await DeliveryWorker(store, send, clock=lambda: now[0]).process_due()
    failed = await store.failed_deliveries()
    assert len(failed) == 2 and all(row["attempts"] == 5 for row in failed)


async def test_retry_after_uses_later_safe_time(store):
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9}))

    async def send(*args, **kwargs):
        raise api_error(TelegramRetryAfter, retry_after=120)

    await DeliveryWorker(store, send, clock=lambda: 1000).process_due()
    assert not await store.due_deliveries(1120)
    assert len(await store.due_deliveries(1121)) == 2


async def test_permanent_failure_visible_and_manual_retry(store):
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9}))

    async def forbidden(*args, **kwargs):
        raise api_error(TelegramForbiddenError)

    await DeliveryWorker(store, forbidden, clock=lambda: 1000).process_due()
    failures = await store.failed_deliveries()
    assert len(failures) == 2 and all(f["attempts"] == 1 for f in failures)
    assert await store.retry_delivery(failures[0]["id"])
    assert len(await store.due_deliveries(1000)) == 1


async def test_invalid_token_stops_worker_without_spending_attempts(store):
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9, 10}))

    async def unauthorized(*args, **kwargs):
        raise api_error(TelegramUnauthorizedError)

    worker = DeliveryWorker(store, unauthorized, clock=lambda: 1000)
    await worker.process_due()
    assert worker.auth_failed
    assert all(r["attempts"] == 0 for r in await store.due_deliveries(1000))


async def test_single_sender_does_not_send_same_delivery_concurrently(store):
    did = await filled_draft(store)
    await store.confirm(did, 1, frozenset({9}))
    sent = []

    async def send(recipient, text, **kwargs):
        sent.append(recipient)
        await asyncio.sleep(0)

    worker = DeliveryWorker(store, send, clock=lambda: 1000)
    await asyncio.gather(worker.process_due(), worker.process_due())
    assert sorted(sent) == [1, 9]
    assert not await store.due_deliveries(1000)
