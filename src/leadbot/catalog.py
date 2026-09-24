"""Replaceable demo catalog and customer-facing labels."""

BUSINESS_NAME = "Выездные услуги · демо"
SERVICES = {
    "plumbing": "Сантехника",
    "electrical": "Электрика",
    "furniture": "Сборка мебели",
    "repair": "Диагностика и мелкий ремонт",
}
FIELDS = ("service", "address", "preferred_time", "name", "phone", "comment")
LABELS = {
    "service": "Услуга",
    "address": "Адрес",
    "preferred_time": "Желаемое время",
    "name": "Имя",
    "phone": "Телефон",
    "comment": "Комментарий",
}
PROMPTS = {
    "address": "Укажите адрес выезда.",
    "preferred_time": "Когда вам удобно? Укажите дату или временной интервал.",
    "name": "Как к вам обращаться?",
    "phone": "Укажите телефон для связи.",
    "comment": "Добавьте комментарий или нажмите «Пропустить».",
}
