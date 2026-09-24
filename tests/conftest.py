from pathlib import Path

import pytest

from leadbot.db import Store


@pytest.fixture
async def store(tmp_path: Path):
    db = Store(tmp_path / "leads.sqlite3", clock=lambda: 1000)
    await db.init()
    return db


async def filled_draft(store: Store, user_id: int = 1, chat_id: int | None = None) -> int:
    did = await store.create_draft(user_id, chat_id or user_id)
    values = {
        "service": "plumbing",
        "address": "Тестовая 1",
        "preferred_time": "Завтра",
        "name": "Тест",
        "phone": "+7 (999) 123-45-67",
        "comment": "",
    }
    for field, value in values.items():
        from leadbot.domain import validate_field

        await store.save_field(did, user_id, field, validate_field(field, value))
    return did
