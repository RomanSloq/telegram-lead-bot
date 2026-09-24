"""Small deterministic rules independent of Telegram and SQLite."""

import re

from leadbot.catalog import FIELDS, SERVICES


def normalize_phone(value: str) -> str | None:
    raw = value.strip()
    if not re.fullmatch(r"\+?[\d\s()\-]+", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        return None
    return ("+" if raw.startswith("+") else "") + digits


def validate_field(field: str, value: str) -> str | None:
    value = value.strip()
    if field == "service":
        return value if value in SERVICES else None
    if field == "phone":
        return normalize_phone(value)
    if field == "comment":
        return value[:500] if len(value) <= 500 else None
    if field in FIELDS and 1 <= len(value) <= 200:
        return value
    return None


def next_step(field: str) -> str:
    i = FIELDS.index(field)
    return FIELDS[i + 1] if i + 1 < len(FIELDS) else "review"


def previous_step(step: str) -> str:
    if step == "review":
        return FIELDS[-1]
    return FIELDS[max(0, FIELDS.index(step) - 1)]
