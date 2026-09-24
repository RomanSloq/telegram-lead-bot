"""One logical sender for persisted deliveries; clock is injected for fast tests."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

from leadbot.db import Store
from leadbot.presentation import lead_buttons, lead_text

log = logging.getLogger(__name__)
DELAYS = (60, 300, 900, 3600)
Send = Callable[..., Awaitable[object]]


class DeliveryWorker:
    def __init__(self, store: Store, send: Send, clock=None):
        self.store = store
        self.send = send
        self.clock = clock or (lambda: int(time.time()))
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.auth_failed = False

    def notify(self) -> None:
        self.wake.set()

    async def process_due(self) -> None:
        async with self.lock:
            if self.auth_failed:
                return
            for delivery in await self.store.due_deliveries(self.clock()):
                lead = await self.store.lead(delivery["lead_id"])
                if lead is None:
                    # Foreign key prevents this in a healthy database.
                    raise RuntimeError("Delivery references a missing lead")
                text = (
                    lead_text(lead)
                    if delivery["kind"] == "manager"
                    else f"Ваша заявка #{lead['id']} принята. Спасибо!"
                )
                markup = lead_buttons(lead) if delivery["kind"] == "manager" else None
                attempts = delivery["attempts"] + 1
                try:
                    await self.send(delivery["recipient"], text, reply_markup=markup)
                except TelegramUnauthorizedError:
                    self.auth_failed = True
                    log.error("Delivery worker stopped: bot authorization failed")
                    return
                except TelegramRetryAfter as exc:
                    await self._fail(delivery["id"], attempts, "rate_limit", exc.retry_after)
                except (TelegramNetworkError, TelegramServerError, OSError, TimeoutError):
                    await self._fail(delivery["id"], attempts, "temporary")
                except (TelegramForbiddenError, TelegramAPIError):
                    await self._fail(delivery["id"], attempts, "recipient_or_request")
                except Exception:
                    # Unknown send errors are uncertain: Telegram may have accepted the message.
                    log.error("Unexpected delivery send failure for delivery %s", delivery["id"])
                    await self._fail(delivery["id"], attempts, "unknown")
                else:
                    await self.store.mark_delivery(delivery["id"], "sent", attempts, self.clock())

    async def _fail(self, delivery_id: int, attempts: int, category: str, retry_after: int = 0) -> None:
        temporary = category in ("temporary", "rate_limit", "unknown")
        if temporary and attempts < 5:
            delay = max(DELAYS[attempts - 1], retry_after + 1)
            await self.store.mark_delivery(delivery_id, "retry", attempts, self.clock() + delay, category)
        else:
            await self.store.mark_delivery(delivery_id, "failed", attempts, self.clock(), category)
            log.warning("Delivery %s failed permanently (%s)", delivery_id, category)

    async def run(self) -> None:
        self.notify()  # Recover due work on startup.
        while not self.auth_failed:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except TimeoutError:
                pass
            self.wake.clear()
            try:
                await self.process_due()
            except Exception:
                # Keep the worker alive when SQLite or another local dependency is temporarily down.
                log.error("Delivery worker cycle failed; will retry")
