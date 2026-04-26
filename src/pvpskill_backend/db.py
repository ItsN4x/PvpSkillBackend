from __future__ import annotations

import contextlib
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    mc_uuid        TEXT PRIMARY KEY,
    mc_name        TEXT NOT NULL,
    discord_id     TEXT NOT NULL UNIQUE,
    linked_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_links_discord ON links(discord_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    subject     TEXT NOT NULL,
    detail      TEXT,
    at          INTEGER NOT NULL
);
"""


class Database:
    """Thin wrapper around a per-process aiosqlite connection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    # --- Links --------------------------------------------------------------

    async def link(self, mc_uuid: str, mc_name: str, discord_id: str, at: int) -> None:
        await self.conn.execute(
            "INSERT INTO links(mc_uuid, mc_name, discord_id, linked_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(mc_uuid) DO UPDATE SET "
            "  mc_name=excluded.mc_name, discord_id=excluded.discord_id, linked_at=excluded.linked_at",
            (mc_uuid, mc_name, discord_id, at),
        )
        await self._audit("link", mc_uuid, f"discord={discord_id}", at)
        await self.conn.commit()

    async def unlink_by_uuid(self, mc_uuid: str, at: int) -> bool:
        cur = await self.conn.execute("DELETE FROM links WHERE mc_uuid = ?", (mc_uuid,))
        changed = cur.rowcount > 0
        await self._audit("unlink_uuid", mc_uuid, "", at)
        await self.conn.commit()
        return changed

    async def unlink_by_discord(self, discord_id: str, at: int) -> bool:
        cur = await self.conn.execute("DELETE FROM links WHERE discord_id = ?", (discord_id,))
        changed = cur.rowcount > 0
        await self._audit("unlink_discord", discord_id, "", at)
        await self.conn.commit()
        return changed

    async def link_by_discord(self, discord_id: str) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT mc_uuid, mc_name, discord_id, linked_at FROM links WHERE discord_id = ?",
            (discord_id,),
        )
        return await cur.fetchone()

    async def link_by_uuid(self, mc_uuid: str) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT mc_uuid, mc_name, discord_id, linked_at FROM links WHERE mc_uuid = ?",
            (mc_uuid,),
        )
        return await cur.fetchone()

    async def stats(self) -> dict[str, int]:
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM links")
        row = await cur.fetchone()
        return {"linked": int(row["n"]) if row else 0}

    async def _audit(self, action: str, subject: str, detail: str, at: int) -> None:
        await self.conn.execute(
            "INSERT INTO audit_log(action, subject, detail, at) VALUES (?, ?, ?, ?)",
            (action, subject, detail, at),
        )


@contextlib.asynccontextmanager
async def open_database(path: Path) -> AsyncIterator[Database]:
    db = Database(path)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()
