"""Application composition and standard aiogram long polling."""

import asyncio
import logging
import re
import traceback
from pathlib import Path

from aiogram import Bot, Dispatcher

from leadbot.config import Config, load_config
from leadbot.db import Store
from leadbot.delivery import DeliveryWorker
from leadbot.handlers.customer import customer_router
from leadbot.handlers.manager import manager_router

_BOT_API_URL = re.compile(r"https?://api\.telegram\.org/(?:file/)?bot[^/\s]+", re.I)


def install_log_redaction(token: str) -> None:
    previous_factory = logging.getLogRecordFactory()

    def redact(value: str) -> str:
        return _BOT_API_URL.sub("[Telegram Bot API URL]", value.replace(token, "[BOT_TOKEN]"))

    def record_factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        if isinstance(record.args, tuple):
            record.args = tuple(
                type(arg).__name__ if isinstance(arg, BaseException) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: type(arg).__name__ if isinstance(arg, BaseException) else arg
                for key, arg in record.args.items()
            }
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            exception_type, _, tb = record.exc_info
            frames = traceback.extract_tb(tb)[-5:]
            location = " -> ".join(
                f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}" for frame in frames
            )
            record.exc_text = redact(f"{exception_type.__name__} at {location}")
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return record

    logging.setLogRecordFactory(record_factory)


async def run(config: Config) -> None:
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
    try:
        config = load_config()
    except ValueError:
        raise SystemExit("Invalid local configuration; check BOT_TOKEN, MANAGER_IDS, DATABASE_PATH") from None
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    install_log_redaction(config.bot_token)
    try:
        asyncio.run(run(config))
    except Exception:
        logging.getLogger(__name__).exception("Bot stopped unexpectedly")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
