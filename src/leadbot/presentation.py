"""Telegram text and buttons for persisted lead data."""

import unicodedata

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from leadbot.catalog import LABELS, SERVICES

STATUS_LABELS = {"new": "Новая", "in_progress": "В работе", "done": "Завершена", "cancelled": "Отменена"}


def service_label(key: str) -> str:
    return SERVICES.get(key, key)


_BIDI_CONTROLS = {
    chr(code) for code in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x2070))
}


def _display(value: str) -> str:
    raw = str(value) if value else "—"
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    clean = "".join(
        char
        if char == "\n" or (unicodedata.category(char) not in {"Cc", "Cs"} and char not in _BIDI_CONTROLS)
        else " "
        for char in raw
    )
    return "\n".join(f"  │ {line}" for line in clean.splitlines())


def lead_text(lead) -> str:
    return "\n".join(
        [
            f"Заявка #{lead['id']} · {STATUS_LABELS[lead['status']]}",
            f"Услуга:\n{_display(service_label(lead['service']))}",
            f"Адрес:\n{_display(lead['address'])}",
            f"Желаемое время:\n{_display(lead['preferred_time'])}",
            f"Имя:\n{_display(lead['name'])}",
            f"Телефон:\n{_display(lead['phone'])}",
            f"Комментарий:\n{_display(lead['comment'])}",
            f"Заметка:\n{_display(lead['note'])}",
        ]
    )


def lead_buttons(lead) -> InlineKeyboardMarkup:
    lid, current = lead["id"], lead["status"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять в работу", callback_data=f"m:status:{lid}:{current}:in_progress"
                )
            ],
            [InlineKeyboardButton(text="Завершить", callback_data=f"m:status:{lid}:{current}:done")],
            [InlineKeyboardButton(text="Отменить", callback_data=f"m:status:{lid}:{current}:cancelled")],
            [InlineKeyboardButton(text="Добавить/изменить заметку", callback_data=f"m:note:{lid}")],
        ]
    )


def review_text(draft) -> str:
    return "Проверьте заявку:\n" + "\n".join(
        f"{LABELS[field]}:\n{_display(service_label(draft[field]) if field == 'service' else draft[field])}"
        for field in LABELS
    )
