"""Customer questionnaire backed by SQLite drafts."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from leadbot.catalog import BUSINESS_NAME, FIELDS, LABELS, PROMPTS, SERVICES
from leadbot.db import Store
from leadbot.delivery import DeliveryWorker
from leadbot.domain import parse_id, previous_step, validate_field
from leadbot.presentation import review_text


def keyboard(*rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows
        ]
    )


def customer_router(store: Store, worker: DeliveryWorker, manager_ids: frozenset[int]) -> Router:
    router = Router(name="customer")

    async def show_step(message: Message, draft) -> None:
        step, did = draft["step"], draft["id"]
        if step == "service":
            rows = [[(label, f"c:service:{did}:{key}")] for key, label in SERVICES.items()]
            await message.answer("Выберите услугу:", reply_markup=keyboard(*rows))
        elif step == "review":
            rows = [[(f"Изменить: {LABELS[field]}", f"c:edit:{did}:{field}")] for field in FIELDS]
            rows += [
                [("Подтвердить", f"c:confirm:{did}")],
                [("Назад", f"c:back:{did}"), ("Отменить", f"c:cancel:{did}")],
            ]
            await message.answer(review_text(draft), reply_markup=keyboard(*rows))
        else:
            rows = [[("Назад", f"c:back:{did}")]]
            if step == "comment":
                rows.insert(0, [("Пропустить", f"c:skip:{did}")])
            await message.answer(PROMPTS[step], reply_markup=keyboard(*rows))

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.chat.type != "private":
            return
        uid = message.from_user.id
        draft = await store.active_draft(uid)
        if draft:
            await message.answer(
                "У вас есть незавершённая заявка. Продолжить или начать заново?",
                reply_markup=keyboard(
                    [("Продолжить", f"c:resume:{draft['id']}")],
                    [("Начать заново", f"c:restart:{draft['id']}")],
                ),
            )
            return
        did = await store.create_draft(uid, message.chat.id)
        await message.answer(f"{BUSINESS_NAME}. Оформим заявку.")
        await show_step(message, await store.draft(did, uid))

    @router.message(Command("cancel"))
    async def cancel(message: Message) -> None:
        if message.chat.type != "private":
            return
        changed = await store.cancel_draft(message.from_user.id)
        await message.answer("Черновик отменён." if changed else "Активного черновика нет.")

    @router.callback_query(F.data.startswith("c:"))
    async def action(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
            await callback.answer("Откройте бот в личном чате.", show_alert=True)
            return
        parts = callback.data.split(":")
        did = parse_id(parts[2]) if len(parts) >= 3 else None
        if did is None:
            await callback.answer("Кнопка устарела.", show_alert=True)
            return
        uid = callback.from_user.id
        draft = await store.draft(did, uid)
        if parts[1] == "confirm":
            try:
                result = await store.confirm(did, uid, manager_ids)
            except Exception:
                await callback.answer(
                    "Не удалось сохранить заявку. Повторите подтверждение.", show_alert=True
                )
                return
            if not result:
                await callback.answer("Заполните и проверьте заявку.", show_alert=True)
                return
            lead_id, created = result
            if created:
                worker.notify()
            await callback.answer()
            await callback.message.answer(f"Заявка #{lead_id} сохранена.")
            return
        if not draft or draft["status"] != "active":
            await callback.answer("Эта кнопка больше не действует.", show_alert=True)
            return
        op = parts[1]
        if op == "resume":
            pass
        elif op == "restart":
            did = await store.create_draft(uid, callback.message.chat.id, replace=True)
        elif op == "cancel":
            await store.cancel_draft(uid)
            await callback.answer()
            await callback.message.answer("Черновик отменён.")
            return
        elif op == "back":
            await store.set_step(did, uid, previous_step(draft["step"]))
        elif op == "edit" and len(parts) == 4 and parts[3] in FIELDS and draft["step"] == "review":
            await store.set_step(did, uid, parts[3], editing=True)
        elif op == "service" and len(parts) == 4 and draft["step"] == "service":
            value = validate_field("service", parts[3])
            if value is None or await store.save_field(did, uid, "service", value) is None:
                await callback.answer("Выберите услугу заново.", show_alert=True)
                return
        elif op == "skip" and draft["step"] == "comment":
            await store.save_field(did, uid, "comment", "")
        else:
            await callback.answer("Кнопка устарела.", show_alert=True)
            return
        await callback.answer()
        await show_step(callback.message, await store.draft(did, uid))

    @router.message(F.text)
    async def input_field(message: Message) -> None:
        if message.chat.type != "private" or message.from_user.id in manager_ids:
            return
        if message.text.startswith("/"):
            await message.answer("Используйте /start или /cancel.")
            return
        draft = await store.active_draft(message.from_user.id)
        if not draft:
            await message.answer("Начните заявку командой /start.")
            return
        field = draft["step"]
        if field in ("service", "review"):
            await show_step(message, draft)
            return
        value = validate_field(field, message.text)
        if value is None:
            await message.answer("Проверьте значение. Для телефона допустимы + и 10–15 цифр.")
            return
        if await store.save_field(draft["id"], message.from_user.id, field, value) is None:
            await message.answer("Шаг изменился. Используйте /start, чтобы продолжить.")
            return
        await show_step(message, await store.draft(draft["id"], message.from_user.id))

    return router
