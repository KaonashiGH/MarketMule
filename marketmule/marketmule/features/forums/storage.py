"""The forums feature's tables: which forms each forum channel offers, and every post the bot has created."""

from __future__ import annotations

import time
from dataclasses import dataclass

from marketmule.core.database import Database

# Append new migrations to the end. Never edit one that has been deployed (see core/database.py).
MIGRATIONS: tuple[str, ...] = (
    # 1: initial tables. Each forum channel had exactly one form.
    """
    CREATE TABLE IF NOT EXISTS forum_bindings (
        channel_id      INTEGER PRIMARY KEY,
        guild_id        INTEGER NOT NULL,
        template_id     TEXT    NOT NULL,
        webhook_url     TEXT,
        panel_thread_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS forum_posts (
        thread_id   INTEGER PRIMARY KEY,
        channel_id  INTEGER NOT NULL,
        template_id TEXT    NOT NULL,
        number      INTEGER NOT NULL,
        reporter_id INTEGER NOT NULL,
        status      TEXT    NOT NULL,
        created_at  INTEGER NOT NULL
    );
    """,
    # 2: a forum channel can offer several forms (for example loadouts and trade-ups).
    #    Existing channels keep their one form.
    """
    CREATE TABLE forum_channel_forms (
        channel_id  INTEGER NOT NULL,
        template_id TEXT    NOT NULL,
        position    INTEGER NOT NULL,
        PRIMARY KEY (channel_id, template_id)
    );

    INSERT INTO forum_channel_forms (channel_id, template_id, position)
        SELECT channel_id, template_id, 0 FROM forum_bindings;

    ALTER TABLE forum_bindings DROP COLUMN template_id;
    """,
)


@dataclass(frozen=True)
class Binding:
    """A forum channel set up with one or more forms, listed in the order they appear in its pinned post."""

    channel_id: int
    guild_id: int
    template_ids: tuple[str, ...]
    webhook_url: str | None
    panel_thread_id: int | None


@dataclass(frozen=True)
class Post:
    thread_id: int
    channel_id: int
    template_id: str
    number: int
    reporter_id: int
    status: str


class ForumStorage:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def migrate(self) -> None:
        await self._db.migrate("forums", MIGRATIONS)

    # Channels and their forms

    async def get_binding(self, channel_id: int) -> Binding | None:
        row = await self._db.fetch_one(
            "SELECT channel_id, guild_id, webhook_url, panel_thread_id FROM forum_bindings WHERE channel_id = ?",
            channel_id,
        )
        return await self._with_forms(row) if row else None

    async def list_bindings(self, guild_id: int) -> list[Binding]:
        rows = await self._db.fetch_all(
            "SELECT channel_id, guild_id, webhook_url, panel_thread_id FROM forum_bindings WHERE guild_id = ?",
            guild_id,
        )
        return [await self._with_forms(row) for row in rows]

    async def _with_forms(self, row: tuple) -> Binding:
        channel_id, guild_id, webhook_url, panel_thread_id = row
        forms = await self._db.fetch_all(
            "SELECT template_id FROM forum_channel_forms WHERE channel_id = ? ORDER BY position, template_id",
            channel_id,
        )
        return Binding(
            channel_id=channel_id,
            guild_id=guild_id,
            template_ids=tuple(form[0] for form in forms),
            webhook_url=webhook_url,
            panel_thread_id=panel_thread_id,
        )

    async def save_binding(self, binding: Binding) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO forum_bindings (channel_id, guild_id, webhook_url, panel_thread_id)"
            " VALUES (?, ?, ?, ?)",
            binding.channel_id,
            binding.guild_id,
            binding.webhook_url,
            binding.panel_thread_id,
        )
        await self._db.execute("DELETE FROM forum_channel_forms WHERE channel_id = ?", binding.channel_id)
        for position, template_id in enumerate(binding.template_ids):
            await self._db.execute(
                "INSERT INTO forum_channel_forms (channel_id, template_id, position) VALUES (?, ?, ?)",
                binding.channel_id,
                template_id,
                position,
            )

    async def delete_binding(self, channel_id: int) -> None:
        await self._db.execute("DELETE FROM forum_channel_forms WHERE channel_id = ?", channel_id)
        await self._db.execute("DELETE FROM forum_bindings WHERE channel_id = ?", channel_id)

    # Posts

    async def next_post_number(self, channel_id: int, template_id: str) -> int:
        """Each form numbers its own posts, even when two forms share a channel (LOAD-0001, TRADE-0001)."""
        row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(number), 0) + 1 FROM forum_posts WHERE channel_id = ? AND template_id = ?",
            channel_id,
            template_id,
        )
        return int(row[0]) if row else 1

    async def add_post(self, post: Post) -> None:
        await self._db.execute(
            "INSERT INTO forum_posts (thread_id, channel_id, template_id, number, reporter_id, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            post.thread_id,
            post.channel_id,
            post.template_id,
            post.number,
            post.reporter_id,
            post.status,
            int(time.time()),
        )

    async def get_post(self, thread_id: int) -> Post | None:
        row = await self._db.fetch_one(
            "SELECT thread_id, channel_id, template_id, number, reporter_id, status FROM forum_posts"
            " WHERE thread_id = ?",
            thread_id,
        )
        return Post(*row) if row else None

    async def set_post_status(self, thread_id: int, status: str) -> None:
        await self._db.execute("UPDATE forum_posts SET status = ? WHERE thread_id = ?", status, thread_id)

    async def count_posts(self, channel_id: int) -> int:
        row = await self._db.fetch_one("SELECT COUNT(*) FROM forum_posts WHERE channel_id = ?", channel_id)
        return int(row[0]) if row else 0
