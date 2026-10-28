"""The stalls feature's tables: stall channels and one stall per member.

The stall's content lives in the member's own forum post. The bot only remembers who owns it, its
status, its control message (the Bump / Close buttons under it) and when it was last bumped or edited.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from marketmule.core.database import Database

# Append new migrations to the end. Never edit one that has been deployed (see core/database.py).
MIGRATIONS: tuple[str, ...] = (
    # 1: stall channels and stalls.
    """
    CREATE TABLE IF NOT EXISTS stall_channels (
        channel_id      INTEGER PRIMARY KEY,
        guild_id        INTEGER NOT NULL,
        panel_thread_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS stalls (
        thread_id          INTEGER PRIMARY KEY,
        guild_id           INTEGER NOT NULL,
        channel_id         INTEGER NOT NULL,
        owner_id           INTEGER NOT NULL,
        status             TEXT    NOT NULL,
        control_message_id INTEGER,
        created_at         INTEGER NOT NULL,
        active_at          INTEGER NOT NULL,
        bumped_at          INTEGER NOT NULL,
        closed_by_staff    INTEGER NOT NULL DEFAULT 0,
        UNIQUE (guild_id, owner_id)
    );
    """,
)

OPEN, CLOSED, DELETED = "open", "closed", "deleted"  # deleted: the post is gone, kept to time the next one


def now() -> int:
    return int(time.time())


@dataclass(frozen=True)
class StallChannel:
    channel_id: int
    guild_id: int
    panel_thread_id: int | None


@dataclass
class Stall:
    thread_id: int
    guild_id: int
    channel_id: int
    owner_id: int
    status: str = OPEN
    control_message_id: int | None = None
    created_at: int = 0
    active_at: int = 0  # the last edit or bump by the owner; 30 days after this, the stall closes
    bumped_at: int = 0
    closed_by_staff: bool = False  # only staff can reopen a stall staff closed

    @property
    def is_closed(self) -> bool:
        return self.status == CLOSED

    @property
    def is_deleted(self) -> bool:
        return self.status == DELETED


COLUMNS = (
    "thread_id, guild_id, channel_id, owner_id, status, control_message_id, created_at, active_at, bumped_at,"
    " closed_by_staff"
)


def _stall(row: tuple) -> Stall:
    values = list(row)
    values[9] = bool(values[9])
    return Stall(*values)


class StallStorage:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def migrate(self) -> None:
        await self._db.migrate("stalls", MIGRATIONS)

    # Channels

    async def get_channel(self, channel_id: int) -> StallChannel | None:
        row = await self._db.fetch_one(
            "SELECT channel_id, guild_id, panel_thread_id FROM stall_channels WHERE channel_id = ?", channel_id
        )
        return StallChannel(*row) if row else None

    async def list_channels(self, guild_id: int) -> list[StallChannel]:
        rows = await self._db.fetch_all(
            "SELECT channel_id, guild_id, panel_thread_id FROM stall_channels WHERE guild_id = ?", guild_id
        )
        return [StallChannel(*row) for row in rows]

    async def save_channel(self, channel: StallChannel) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO stall_channels (channel_id, guild_id, panel_thread_id) VALUES (?, ?, ?)",
            channel.channel_id,
            channel.guild_id,
            channel.panel_thread_id,
        )

    async def delete_channel(self, channel_id: int) -> None:
        await self._db.execute("DELETE FROM stall_channels WHERE channel_id = ?", channel_id)

    # Stalls

    async def add_stall(self, stall: Stall) -> None:
        await self._db.execute(
            f"INSERT INTO stalls ({COLUMNS}) VALUES ({', '.join('?' * 10)})",
            stall.thread_id,
            stall.guild_id,
            stall.channel_id,
            stall.owner_id,
            stall.status,
            stall.control_message_id,
            stall.created_at,
            stall.active_at,
            stall.bumped_at,
            int(stall.closed_by_staff),
        )

    async def save_stall(self, stall: Stall) -> None:
        await self._db.execute(
            "UPDATE stalls SET status = ?, control_message_id = ?, active_at = ?, bumped_at = ?, closed_by_staff = ?"
            " WHERE thread_id = ?",
            stall.status,
            stall.control_message_id,
            stall.active_at,
            stall.bumped_at,
            int(stall.closed_by_staff),
            stall.thread_id,
        )

    async def get_stall(self, thread_id: int) -> Stall | None:
        row = await self._db.fetch_one(f"SELECT {COLUMNS} FROM stalls WHERE thread_id = ?", thread_id)
        return _stall(row) if row else None

    async def stall_of(self, guild_id: int, owner_id: int) -> Stall | None:
        row = await self._db.fetch_one(
            f"SELECT {COLUMNS} FROM stalls WHERE guild_id = ? AND owner_id = ?", guild_id, owner_id
        )
        return _stall(row) if row else None

    async def open_stalls(self) -> list[Stall]:
        rows = await self._db.fetch_all(f"SELECT {COLUMNS} FROM stalls WHERE status = ?", OPEN)
        return [_stall(row) for row in rows]

    async def count_stalls(self, channel_id: int) -> tuple[int, int]:
        """(open, closed) stalls in a channel."""
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(status = ?), 0), COALESCE(SUM(status = ?), 0) FROM stalls WHERE channel_id = ?",
            OPEN,
            CLOSED,
            channel_id,
        )
        return (int(row[0]), int(row[1])) if row else (0, 0)

    async def mark_deleted(self, thread_id: int) -> None:
        """The post is gone. Keep the row (with its last bump time) so a new post can't skip the bump wait."""
        await self._db.execute("UPDATE stalls SET status = ? WHERE thread_id = ?", DELETED, thread_id)

    async def delete_stall(self, thread_id: int) -> None:
        """Forget a stall completely, so its owner can post a new one."""
        await self._db.execute("DELETE FROM stalls WHERE thread_id = ?", thread_id)
