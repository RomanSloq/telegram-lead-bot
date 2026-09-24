import logging

import pytest

from leadbot.config import load_config
from leadbot.domain import parse_id
from leadbot.main import install_log_redaction
from leadbot.presentation import lead_text
from tests.test_telegram_flow import Harness, submit


def test_log_redaction_keeps_error_type_without_fake_credentials(caplog):
    fake_token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    old_factory = logging.getLogRecordFactory()
    try:
        install_log_redaction(fake_token)
        with caplog.at_level(logging.ERROR):
            try:
                raise RuntimeError(
                    f"https://api.telegram.org/bot{fake_token}/getUpdates contains private text"
                )
            except RuntimeError as exc:
                logging.getLogger("aiogram.dispatcher").error(
                    "Failed to fetch updates - %s: %s",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
            logging.getLogger("leadbot.test").error(
                "Request failed at https://api.telegram.org/bot%s/sendMessage", fake_token
            )
        output = caplog.text
        assert fake_token not in output
        assert "https://api.telegram.org/bot" not in output
        assert "private text" not in output
        assert "Failed to fetch updates" in output
        assert "RuntimeError" in output
        assert "test_security_hardening.py" in output
    finally:
        logging.setLogRecordFactory(old_factory)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1),
        ("9223372036854775807", 9223372036854775807),
        ("²", None),
        ("１", None),
        ("9" * 25, None),
        ("9223372036854775808", None),
        ("1x", None),
        ("-1", None),
        ("", None),
    ],
)
def test_parse_id_accepts_only_bounded_ascii_digits(raw, expected):
    assert parse_id(raw) == expected


async def test_malformed_manager_ids_do_not_execute_actions(store):
    h = Harness(store)
    await submit(h)
    original_delivery = await store.delivery(1)

    for raw in ("²", "１", "9" * 25, "1x"):
        await h.message(9, f"/lead {raw}")
        await h.message(9, f"/retry_delivery {raw}")
        await h.callback(9, f"m:status:{raw}:new:done")
        await h.callback(9, f"m:note:{raw}")
        await h.callback(1, f"c:confirm:{raw}")

    assert (await store.lead(1))["status"] == "new"
    assert (await store.lead(1))["note"] == ""
    assert (await store.delivery(1))["state"] == original_delivery["state"]
    assert len(await store.leads()) == 1
    await h.message(9, "/lead 1")
    assert any("Заявка #1" in text for text in h.texts())


def test_manager_card_separates_multiline_and_control_text():
    lead = {
        "id": 1,
        "status": "new",
        "service": "plumbing",
        "address": "Адрес 1\nЗаметка: подменена\u202e\x1b",
        "preferred_time": "Завтра\u2028Статус: готово",
        "name": "Имя\x00Телефон: 000",
        "phone": "1234567890",
        "comment": "Первая строка\nВторая строка 👩‍🔧",
        "note": "Позвонить\nАдрес: подмена",
    }
    text = lead_text(lead)
    assert text.count("\nЗаметка:\n") == 1
    assert text.count("\nАдрес:\n") == 1
    assert "  │ Заметка: подменена" in text
    assert "  │ Статус: готово" in text
    assert "  │ Вторая строка 👩‍🔧" in text
    assert "  │ Адрес: подмена" in text
    assert "\u202e" not in text
    assert "\x1b" not in text
    assert "\x00" not in text


def test_manager_ids_configuration_rejects_unicode_digits(monkeypatch, tmp_path):
    monkeypatch.setattr("leadbot.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("MANAGER_IDS", "１")
    with pytest.raises(ValueError, match="MANAGER_IDS"):
        load_config()
    monkeypatch.setenv("MANAGER_IDS", "9")
    assert load_config().manager_ids == frozenset({9})
