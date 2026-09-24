"""Feed real aiogram updates through the routers with an in-memory Telegram API stub."""

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Update

from leadbot.db import Store
from leadbot.delivery import DeliveryWorker
from leadbot.handlers.customer import customer_router
from leadbot.handlers.manager import manager_router


class Harness:
    def __init__(self, store):
        self.store = store
        self.bot = Bot("123:TEST")
        self.sent = []
        self.seq = 0

        async def fake_request(bot, method, timeout=None):
            self.sent.append(method)
            return True

        self.bot.session.make_request = fake_request
        self.worker = DeliveryWorker(store, self.bot.send_message, clock=lambda: 1000)
        self.reset_routers()

    def reset_routers(self):
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(manager_router(self.store, self.worker, frozenset({9})))
        self.dispatcher.include_router(customer_router(self.store, self.worker, frozenset({9})))

    async def message(self, uid: int, text: str):
        self.seq += 1
        update = Update.model_validate(
            {
                "update_id": self.seq,
                "message": {
                    "message_id": self.seq,
                    "date": 1000,
                    "chat": {"id": uid, "type": "private"},
                    "from": {"id": uid, "is_bot": False, "first_name": "Test"},
                    "text": text,
                },
            }
        )
        await self.dispatcher.feed_update(self.bot, update)

    async def callback(self, uid: int, data: str):
        self.seq += 1
        update = Update.model_validate(
            {
                "update_id": self.seq,
                "callback_query": {
                    "id": str(self.seq),
                    "chat_instance": "test",
                    "from": {"id": uid, "is_bot": False, "first_name": "Test"},
                    "message": {
                        "message_id": self.seq,
                        "date": 1000,
                        "chat": {"id": uid, "type": "private"},
                        "from": {"id": 123, "is_bot": True, "first_name": "Bot"},
                        "text": "Test",
                    },
                    "data": data,
                },
            }
        )
        await self.dispatcher.feed_update(self.bot, update)

    def texts(self):
        return [m.text for m in self.sent if isinstance(getattr(m, "text", None), str)]


async def submit(h: Harness, uid: int = 1):
    await h.message(uid, "/start")
    draft = await h.store.active_draft(uid)
    did = draft["id"]
    await h.callback(uid, f"c:service:{did}:plumbing")
    for value in ("Тестовая 1", "Завтра", "Анна", "+7 (999) 123-45-67"):
        await h.message(uid, value)
    await h.callback(uid, f"c:skip:{did}")
    await h.callback(uid, f"c:confirm:{did}")
    return did


async def test_customer_update_flow_and_manager_list(store):
    h = Harness(store)
    did = await submit(h)
    assert (await store.draft(did, 1))["status"] == "confirmed"
    assert len(await store.leads()) == 1
    assert len(await store.due_deliveries(1000)) == 2
    await h.message(9, "/leads")
    assert any("#1" in text for text in h.texts())
    await h.callback(1, f"c:confirm:{did}")
    assert len(await store.leads()) == 1


async def test_manager_buttons_note_and_restart(store):
    h = Harness(store)
    await submit(h)
    await h.message(9, "/lead 1")
    await h.callback(9, "m:status:1:new:in_progress")
    assert (await store.lead(1))["status"] == "in_progress"
    await h.callback(9, "m:note:1")
    await h.message(9, "Позвонить утром")
    assert (await store.lead(1))["note"] == "Позвонить утром"
    await h.callback(9, "m:note:1")
    h.reset_routers()  # Transient manager input disappears after a process restart.
    await h.message(9, "Случайный текст")
    assert (await store.lead(1))["note"] == "Позвонить утром"
    await h.callback(9, "m:note:1")
    await h.message(9, "Новое значение")
    assert (await store.lead(1))["note"] == "Новое значение"


async def test_unauthorized_commands_callbacks_and_notes_do_not_mutate(store):
    h = Harness(store)
    await submit(h)
    await h.message(2, "/leads")
    await h.message(2, "/lead 1")
    await h.message(2, "/delivery_failures")
    await h.message(2, "/retry_delivery 1")
    await h.callback(2, "m:status:1:new:done")
    await h.callback(2, "m:note:1")
    await h.message(2, "Попытка записать заметку")
    assert (await store.lead(1))["status"] == "new"
    assert (await store.lead(1))["note"] == ""
    assert any(text == "Нет доступа." for text in h.texts())


async def test_temporary_manager_delivery_recovers_after_restart_with_same_lead_id(store):
    h = Harness(store)
    draft_id = await submit(h)
    now = [1000]
    committed = Store(store.path, clock=lambda: now[0])
    leads = await committed.leads()
    assert len(leads) == 1
    lead_id = leads[0]["id"]
    assert (await committed.lead(lead_id))["source_draft_id"] == draft_id
    deliveries = await committed.due_deliveries(now[0])
    assert len(deliveries) == 2
    manager_delivery = next(row for row in deliveries if row["kind"] == "manager")
    assert manager_delivery["lead_id"] == lead_id

    manager_attempts = []

    async def send(recipient, text, **kwargs):
        if recipient == 9:
            manager_attempts.append(text)
            if len(manager_attempts) == 1:
                raise TelegramNetworkError(
                    method=SendMessage(chat_id=recipient, text=text), message="temporary"
                )

    await DeliveryWorker(store, send, clock=lambda: now[0]).process_due()
    assert await committed.lead(lead_id)
    retry = await committed.delivery(manager_delivery["id"])
    assert retry["state"] == "retry" and retry["attempts"] == 1
    assert retry["next_attempt_at"] > now[0]

    sent_before = len(h.sent)
    await h.message(9, "/leads")
    replies = [
        method.text for method in h.sent[sent_before:] if isinstance(getattr(method, "text", None), str)
    ]
    assert any("Последние заявки" in reply and f"#{lead_id}" in reply for reply in replies)

    now[0] = retry["next_attempt_at"] + 1
    restarted = Store(store.path, clock=lambda: now[0])
    await DeliveryWorker(restarted, send, clock=lambda: now[0]).process_due()
    delivered = await restarted.delivery(manager_delivery["id"])
    assert delivered["state"] == "sent" and delivered["attempts"] == 2
    assert delivered["lead_id"] == lead_id
    assert len(manager_attempts) == 2
    assert f"Заявка #{lead_id}" in manager_attempts[-1]
    assert len(await restarted.leads()) == 1
    async with restarted.connect() as db:
        async with db.execute("SELECT id,lead_id FROM deliveries WHERE kind='manager'") as cur:
            manager_rows = await cur.fetchall()
    assert [(row["id"], row["lead_id"]) for row in manager_rows] == [(manager_delivery["id"], lead_id)]
