"""Private-chat Mini CRM; every command and callback checks manager identity."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from leadbot.db import Store
from leadbot.delivery import DeliveryWorker
from leadbot.presentation import STATUS_LABELS, lead_buttons, lead_text, service_label


def manager_router(store: Store, worker: DeliveryWorker, manager_ids: frozenset[int]) -> Router:
    router = Router(name="manager")
    pending_notes: dict[int, int] = {}

    def allowed(message: Message) -> bool:
        return message.chat.type == "private" and message.from_user.id in manager_ids

    async def card(message: Message, lead_id: int) -> None:
        lead = await store.lead(lead_id)
        if not lead:
            await message.answer("Заявка не найдена.")
        else:
            await message.answer(lead_text(lead), reply_markup=lead_buttons(lead))

    @router.message(CommandStart(), F.from_user.id.in_(manager_ids))
    async def start_manager(message: Message) -> None:
        if not allowed(message):
            await message.answer("Нет доступа.")
            return
        await message.answer(
            "Режим менеджера. Откройте /leads для заявок или /delivery_failures для ошибок доставки."
        )

    @router.message(Command("leads"))
    async def leads(message: Message) -> None:
        if not allowed(message):
            await message.answer("Нет доступа.")
            return
        items = await store.leads()
        body = "\n".join(
            f"#{lead['id']} · {service_label(lead['service'])} · {STATUS_LABELS[lead['status']]}"
            for lead in items
        )
        await message.answer("Последние заявки:\n" + (body or "Пока нет заявок."))

    @router.message(Command("lead"))
    async def lead(message: Message) -> None:
        if not allowed(message):
            await message.answer("Нет доступа.")
            return
        parts = message.text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Использование: /lead <ID>")
            return
        await card(message, int(parts[1]))

    @router.message(Command("delivery_failures"))
    async def failures(message: Message) -> None:
        if not allowed(message):
            await message.answer("Нет доступа.")
            return
        rows = await store.failed_deliveries()
        body = "\n".join(
            f"Доставка #{row['id']} · заявка #{row['lead_id']} · {row['kind']} · "
            f"{row['attempts']} попыток · {row['error_category']}"
            for row in rows
        )
        await message.answer("Ошибки доставок:\n" + (body or "Нет."))

    @router.message(Command("retry_delivery"))
    async def retry(message: Message) -> None:
        if not allowed(message):
            await message.answer("Нет доступа.")
            return
        parts = message.text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Использование: /retry_delivery <ID доставки>")
            return
        ok = await store.retry_delivery(int(parts[1]))
        if ok:
            worker.notify()
        await message.answer("Доставка возвращена в очередь." if ok else "Нет failed-доставки с этим ID.")

    @router.message(Command("cancel"), F.from_user.id.in_(manager_ids))
    async def cancel_note(message: Message) -> None:
        if not allowed(message):
            await message.answer("Нет доступа.")
            return
        if pending_notes.pop(message.from_user.id, None) is not None:
            await message.answer("Ввод заметки отменён.")
        else:
            ok = await store.cancel_draft(message.from_user.id)
            await message.answer("Черновик отменён." if ok else "Нечего отменять.")

    @router.callback_query(F.data.startswith("m:"))
    async def action(callback: CallbackQuery) -> None:
        if (
            callback.from_user.id not in manager_ids
            or not isinstance(callback.message, Message)
            or callback.message.chat.type != "private"
        ):
            await callback.answer("Нет доступа.", show_alert=True)
            return
        parts = callback.data.split(":")
        if len(parts) < 3 or not parts[2].isdigit():
            await callback.answer("Кнопка устарела.", show_alert=True)
            return
        lead_id = int(parts[2])
        lead = await store.lead(lead_id)
        if not lead:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        if parts[1] == "note" and len(parts) == 3:
            pending_notes[callback.from_user.id] = lead_id
            await callback.answer()
            await callback.message.answer("Отправьте текст заметки следующим сообщением или /cancel.")
        elif parts[1] == "status" and len(parts) == 5:
            ok = await store.change_status(lead_id, parts[3], parts[4])
            if not ok:
                await callback.answer("Карточка устарела. Откройте /lead снова.", show_alert=True)
                return
            await callback.answer("Статус изменён.")
            await card(callback.message, lead_id)
        else:
            await callback.answer("Кнопка устарела.", show_alert=True)

    @router.message(F.text & ~F.text.startswith("/") & F.from_user.id.in_(manager_ids))
    async def note_text(message: Message) -> None:
        if not allowed(message):
            return
        lead_id = pending_notes.get(message.from_user.id)
        if lead_id is None:
            await message.answer("Откройте /leads или /start.")
            return
        if not message.text.strip() or len(message.text) > 1000:
            await message.answer("Заметка должна содержать 1–1000 символов.")
            return
        ok = await store.save_note(lead_id, message.text)
        pending_notes.pop(message.from_user.id, None)
        if ok:
            await card(message, lead_id)
        else:
            await message.answer("Заявка не найдена.")

    return router
