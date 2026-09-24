"""Async SQLite persistence. Critical lead and delivery creation shares one transaction."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from leadbot.catalog import FIELDS

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS drafts (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','confirmed','cancelled')),
 step TEXT NOT NULL DEFAULT 'service', editing INTEGER NOT NULL DEFAULT 0,
 service TEXT, address TEXT, preferred_time TEXT, name TEXT, phone TEXT, comment TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_draft ON drafts(user_id) WHERE status='active';
CREATE TABLE IF NOT EXISTS leads (
 id INTEGER PRIMARY KEY, source_draft_id INTEGER NOT NULL UNIQUE REFERENCES drafts(id),
 user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
 service TEXT NOT NULL, address TEXT NOT NULL, preferred_time TEXT NOT NULL,
 name TEXT NOT NULL, phone TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','done','cancelled')),
 note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
 id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
 kind TEXT NOT NULL CHECK(kind IN ('manager','customer')), recipient INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','retry','sent','failed')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL,
 error_category TEXT, sent_at INTEGER,
 UNIQUE(lead_id,kind,recipient)
);
CREATE INDEX IF NOT EXISTS due_deliveries ON deliveries(state,next_attempt_at,id);
"""


class Store:
    def __init__(self, path: Path, clock=None):
        self.path = path
        self.clock = clock or (lambda: int(time.time()))

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            await db.close()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def active_draft(self, user_id: int):
        async with self.connect() as db:
            async with db.execute(
                "SELECT * FROM drafts WHERE user_id=? AND status='active'", (user_id,)
            ) as cur:
                return await cur.fetchone()

    async def draft(self, draft_id: int, user_id: int):
        async with self.connect() as db:
            async with db.execute(
                "SELECT * FROM drafts WHERE id=? AND user_id=?", (draft_id, user_id)
            ) as cur:
                return await cur.fetchone()

    async def create_draft(self, user_id: int, chat_id: int, replace: bool = False):
        now = self.clock()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if replace:
                await db.execute(
                    "UPDATE drafts SET status='cancelled',updated_at=? WHERE user_id=? AND status='active'",
                    (now, user_id),
                )
            else:
                async with db.execute(
                    "SELECT id FROM drafts WHERE user_id=? AND status='active'", (user_id,)
                ) as cur:
                    existing = await cur.fetchone()
                if existing:
                    await db.commit()
                    return existing["id"]
            cur = await db.execute(
                "INSERT INTO drafts(user_id,chat_id,created_at,updated_at) VALUES(?,?,?,?)",
                (user_id, chat_id, now, now),
            )
            await db.commit()
            return cur.lastrowid

    async def cancel_draft(self, user_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE drafts SET status='cancelled',updated_at=? WHERE user_id=? AND status='active'",
                (self.clock(), user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def set_step(self, draft_id: int, user_id: int, step: str, editing: bool = False):
        if step not in (*FIELDS, "review"):
            raise ValueError("Unknown step")
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE drafts SET step=?,editing=?,updated_at=? "
                "WHERE id=? AND user_id=? AND status='active'",
                (step, int(editing), self.clock(), draft_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def save_field(self, draft_id: int, user_id: int, field: str, value: str):
        if field not in FIELDS:
            raise ValueError("Unknown field")
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT step,editing FROM drafts WHERE id=? AND user_id=? AND status='active'",
                (draft_id, user_id),
            ) as cur:
                draft = await cur.fetchone()
            if not draft or draft["step"] != field:
                await db.rollback()
                return None
            step = (
                "review"
                if draft["editing"]
                else (FIELDS[FIELDS.index(field) + 1] if field != FIELDS[-1] else "review")
            )
            await db.execute(
                f"UPDATE drafts SET {field}=?,step=?,editing=0,updated_at=? WHERE id=?",
                (value, step, self.clock(), draft_id),
            )
            await db.commit()
            return step

    async def confirm(self, draft_id: int, user_id: int, manager_ids: frozenset[int]):
        now = self.clock()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT id FROM leads WHERE source_draft_id=? AND user_id=?", (draft_id, user_id)
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                await db.commit()
                return existing["id"], False
            async with db.execute(
                "SELECT * FROM drafts WHERE id=? AND user_id=? AND status='active'",
                (draft_id, user_id),
            ) as cur:
                draft = await cur.fetchone()
            if not draft or draft["step"] != "review" or any(draft[field] is None for field in FIELDS):
                await db.rollback()
                return None
            cur = await db.execute(
                "INSERT INTO leads(source_draft_id,user_id,chat_id,service,address,"
                "preferred_time,name,phone,comment,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (draft_id, user_id, draft["chat_id"], *(draft[field] for field in FIELDS), now, now),
            )
            lead_id = cur.lastrowid
            for kind, recipient in [
                *(("manager", mid) for mid in sorted(manager_ids)),
                ("customer", draft["chat_id"]),
            ]:
                await db.execute(
                    "INSERT INTO deliveries(lead_id,kind,recipient,next_attempt_at) VALUES(?,?,?,?)",
                    (lead_id, kind, recipient, now),
                )
            await db.execute("UPDATE drafts SET status='confirmed',updated_at=? WHERE id=?", (now, draft_id))
            await db.commit()
            return lead_id, True

    async def lead(self, lead_id: int):
        async with self.connect() as db:
            async with db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)) as cur:
                return await cur.fetchone()

    async def leads(self, limit: int = 20):
        async with self.connect() as db:
            async with db.execute(
                "SELECT id,service,status,created_at FROM leads ORDER BY id DESC LIMIT ?", (limit,)
            ) as cur:
                return await cur.fetchall()

    async def change_status(self, lead_id: int, expected: str, status: str) -> bool:
        if status not in ("new", "in_progress", "done", "cancelled"):
            return False
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE leads SET status=?,updated_at=? WHERE id=? AND status=?",
                (status, self.clock(), lead_id, expected),
            )
            await db.commit()
            return cur.rowcount > 0

    async def save_note(self, lead_id: int, note: str) -> bool:
        if not note.strip() or len(note) > 1000:
            return False
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE leads SET note=?,updated_at=? WHERE id=?",
                (note.strip(), self.clock(), lead_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def due_deliveries(self, now: int):
        async with self.connect() as db:
            async with db.execute(
                "SELECT * FROM deliveries WHERE state IN ('pending','retry') "
                "AND next_attempt_at<=? ORDER BY next_attempt_at,id",
                (now,),
            ) as cur:
                return await cur.fetchall()

    async def delivery(self, delivery_id: int):
        async with self.connect() as db:
            async with db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)) as cur:
                return await cur.fetchone()

    async def mark_delivery(
        self, delivery_id: int, state: str, attempts: int, next_attempt_at: int, category: str | None = None
    ):
        if state not in ("retry", "sent", "failed"):
            raise ValueError("Invalid delivery state")
        async with self.connect() as db:
            await db.execute(
                "UPDATE deliveries SET state=?,attempts=?,next_attempt_at=?,"
                "error_category=?,sent_at=? WHERE id=?",
                (
                    state,
                    attempts,
                    next_attempt_at,
                    category,
                    self.clock() if state == "sent" else None,
                    delivery_id,
                ),
            )
            await db.commit()

    async def failed_deliveries(self):
        async with self.connect() as db:
            async with db.execute(
                "SELECT id,lead_id,kind,attempts,error_category FROM deliveries "
                "WHERE state='failed' ORDER BY id DESC"
            ) as cur:
                return await cur.fetchall()

    async def retry_delivery(self, delivery_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                "UPDATE deliveries SET state='pending',attempts=0,next_attempt_at=?,"
                "error_category=NULL,sent_at=NULL WHERE id=? AND state='failed'",
                (self.clock(), delivery_id),
            )
            await db.commit()
            return cur.rowcount > 0
