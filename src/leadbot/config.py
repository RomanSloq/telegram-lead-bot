"""Fail-fast configuration without exposing token values."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    manager_ids: frozenset[int]
    database_path: Path


def load_config() -> Config:
    load_dotenv(override=False)
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "replace-with-telegram-bot-token" or ":" not in token:
        raise ValueError("BOT_TOKEN is missing or invalid; set it in local .env")
    raw_ids = os.getenv("MANAGER_IDS", "").strip()
    try:
        ids = frozenset(int(part.strip()) for part in raw_ids.split(","))
        if not ids or any(i <= 0 for i in ids):
            raise ValueError
    except ValueError as exc:
        raise ValueError("MANAGER_IDS must be comma-separated positive Telegram user IDs") from exc
    path = Path(os.getenv("DATABASE_PATH", "leadbot.sqlite3"))
    if path.exists() and path.is_dir():
        raise ValueError("DATABASE_PATH points to a directory")
    return Config(token, ids, path)
