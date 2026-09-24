"""Telegram text and buttons for persisted lead data."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from leadbot.catalog import LABELS, SERVICES

STATUS_LABELS = {"new": "Новая", "in_progress": "В работе", "done": "Завершена", "cancelled": "Отменена"}


def service_label(key: str) -> str:
    return SERVICES.get(key, key)


def lead_text(lead) -> str:
    return "\n".join(
        [
            f"Заявка #{lead['id']} · {STATUS_LABELS[lead['status']]}",
            f"Услуга: {service_label(lead['service'])}",
            f"Адрес: {lead['address']}",
            f"Желаемое время: {lead['preferred_time']}",
            f"Имя: {lead['name']}",
            f"Телефон: {lead['phone']}",
            f"Комментарий: {lead['comment'] or '—'}",
            f"Заметка: {lead['note'] or '—'}",
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
        f"{LABELS[field]}: {service_label(draft[field]) if field == 'service' else draft[field] or '—'}"
        for field in LABELS
    )
