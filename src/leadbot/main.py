"""Application composition and standard aiogram long polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from leadbot.config import load_config
from leadbot.db import Store
from leadbot.delivery import DeliveryWorker
from leadbot.handlers.customer import customer_router
from leadbot.handlers.manager import manager_router


async def run() -> None:
    config = load_config()
    store = Store(config.database_path)
    await store.init()
    bot = Bot(config.bot_token)
    worker = DeliveryWorker(store, bot.send_message)
    dispatcher = Dispatcher()
    dispatcher.include_router(manager_router(store, worker, config.manager_ids))
    dispatcher.include_router(customer_router(store, worker, config.manager_ids))
    task = asyncio.create_task(worker.run(), name="delivery-worker")
    try:
        await dispatcher.start_polling(bot, handle_as_tasks=False)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
