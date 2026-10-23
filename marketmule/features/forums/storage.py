"""The forums feature's tables: which forms each forum channel offers, and every post the bot has created."""

from __future__ import annotations

import json
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
    # 3: member ratings (one per member per post) and the bot message that shows a post's average.
    """
    CREATE TABLE forum_ratings (
        thread_id  INTEGER NOT NULL,
        rater_id   INTEGER NOT NULL,
        scores     TEXT    NOT NULL,
        total      INTEGER NOT NULL,
        verdict    TEXT,
        note       TEXT,
        message_id INTEGER,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (thread_id, rater_id)
    );

    ALTER TABLE forum_posts ADD COLUMN rating_message_id INTEGER;
    """,
    # 4: members staff have blocked from rating, per server.
    """
    CREATE TABLE forum_rating_blocks (
        guild_id   INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        reason     TEXT,
        blocked_by INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
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


@dataclass(frozen=True)
class MemberRating:
    """One member's rating of one post. Rating again replaces it."""

    thread_id: int
    rater_id: int
    scores: dict[str, int]
    verdict: str | None = None
    note: str | None = None
    message_id: int | None = None

    @property
    def total(self) -> int:
        return sum(self.scores.values())


@dataclass(frozen=True)
class RatingBlock:
    user_id: int
    reason: str | None
    blocked_by: int


@dataclass(frozen=True)
class RatingSummary:
    average: float = 0.0
    count: int = 0


@dataclass(frozen=True)
class RankedPost:
    thread_id: int
    reporter_id: int
    average: float
    count: int


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

    # Ratings

    async def set_rating_message(self, thread_id: int, message_id: int) -> None:
        await self._db.execute(
            "UPDATE forum_posts SET rating_message_id = ? WHERE thread_id = ?", message_id, thread_id
        )

    async def get_rating_message(self, thread_id: int) -> int | None:
        row = await self._db.fetch_one("SELECT rating_message_id FROM forum_posts WHERE thread_id = ?", thread_id)
        return int(row[0]) if row and row[0] else None

    async def get_rating(self, thread_id: int, rater_id: int) -> MemberRating | None:
        row = await self._db.fetch_one(
            "SELECT thread_id, rater_id, scores, verdict, note, message_id FROM forum_ratings"
            " WHERE thread_id = ? AND rater_id = ?",
            thread_id,
            rater_id,
        )
        if row is None:
            return None
        return MemberRating(row[0], row[1], json.loads(row[2]), row[3], row[4], row[5])

    async def save_rating(self, rating: MemberRating) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO forum_ratings"
            " (thread_id, rater_id, scores, total, verdict, note, message_id, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rating.thread_id,
            rating.rater_id,
            json.dumps(rating.scores),
            rating.total,
            rating.verdict,
            rating.note,
            rating.message_id,
            int(time.time()),
        )

    async def rating_summary(self, thread_id: int) -> RatingSummary:
        row = await self._db.fetch_one("SELECT AVG(total), COUNT(*) FROM forum_ratings WHERE thread_id = ?", thread_id)
        if not row or not row[1]:
            return RatingSummary()
        return RatingSummary(average=float(row[0]), count=int(row[1]))

    async def top_rated(self, template_id: str, min_ratings: int, limit: int = 10) -> list[RankedPost]:
        """Highest average first. Ties go to the post with more ratings."""
        rows = await self._db.fetch_all(
            "SELECT p.thread_id, p.reporter_id, AVG(r.total) AS average, COUNT(*) AS count"
            " FROM forum_ratings r JOIN forum_posts p ON p.thread_id = r.thread_id"
            " WHERE p.template_id = ?"
            " GROUP BY p.thread_id HAVING COUNT(*) >= ?"
            " ORDER BY average DESC, count DESC LIMIT ?",
            template_id,
            min_ratings,
            limit,
        )
        return [RankedPost(int(r[0]), int(r[1]), float(r[2]), int(r[3])) for r in rows]

    async def find_rating_by_message(self, message_id: int) -> MemberRating | None:
        """The rating shown by a message, so staff can remove it from the message itself."""
        row = await self._db.fetch_one("SELECT thread_id, rater_id FROM forum_ratings WHERE message_id = ?", message_id)
        return await self.get_rating(row[0], row[1]) if row else None

    async def delete_rating(self, thread_id: int, rater_id: int) -> None:
        await self._db.execute("DELETE FROM forum_ratings WHERE thread_id = ? AND rater_id = ?", thread_id, rater_id)

    # Members blocked from rating

    async def block_rater(self, guild_id: int, user_id: int, reason: str | None, blocked_by: int) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO forum_rating_blocks (guild_id, user_id, reason, blocked_by, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            guild_id,
            user_id,
            reason,
            blocked_by,
            int(time.time()),
        )

    async def unblock_rater(self, guild_id: int, user_id: int) -> bool:
        """Returns False if the member wasn't blocked."""
        was_blocked = await self.is_rater_blocked(guild_id, user_id)
        await self._db.execute("DELETE FROM forum_rating_blocks WHERE guild_id = ? AND user_id = ?", guild_id, user_id)
        return was_blocked

    async def is_rater_blocked(self, guild_id: int, user_id: int) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 FROM forum_rating_blocks WHERE guild_id = ? AND user_id = ?", guild_id, user_id
        )
        return row is not None

    async def list_rating_blocks(self, guild_id: int) -> list[RatingBlock]:
        rows = await self._db.fetch_all(
            "SELECT user_id, reason, blocked_by FROM forum_rating_blocks WHERE guild_id = ? ORDER BY created_at",
            guild_id,
        )
        return [RatingBlock(int(r[0]), r[1], int(r[2])) for r in rows]
