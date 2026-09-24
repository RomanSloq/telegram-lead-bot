import pytest

from leadbot.config import load_config
from tests.test_telegram_flow import Harness


def test_configuration_rejects_missing_token_and_bad_manager_ids(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("MANAGER_IDS", "9")
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        load_config()
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("MANAGER_IDS", "not-an-id")
    with pytest.raises(ValueError, match="MANAGER_IDS"):
        load_config()


async def test_manager_can_open_bot_without_starting_customer_draft(store):
    h = Harness(store)
    await h.message(9, "/start")
    assert await store.active_draft(9) is None
    assert any("Режим менеджера" in text for text in h.texts())
