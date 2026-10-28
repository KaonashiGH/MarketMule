"""Stalls against a pretend Discord: members' posts, removals, edits, bumps, closing, reopening and upkeep.

The fakes record what the bot does (messages sent and deleted, DMs, tags, lock/archive), so these tests
check real behaviour of the event handlers and service code without a Discord connection.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from marketmule.core.config import PROJECT_ROOT
from marketmule.core.database import Database
from marketmule.features.stalls import cog as cog_module
from marketmule.features.stalls import service
from marketmule.features.stalls.cog import Stalls
from marketmule.features.stalls.context import StallContext
from marketmule.features.stalls.settings import load_settings
from marketmule.features.stalls.storage import CLOSED, OPEN, StallChannel, StallStorage
from marketmule.features.stalls.views import ControlView, ReopenView

SETTINGS = load_settings(PROJECT_ROOT / "templates" / "stalls" / "stalls.json")
HOUR = 3600
BOT_ID = 999
GOOD_POST = "🏪 WELCOME TO MY STALL\nStore: https://csfloat.com/stall/7656\nKarambit Doppler P2 - $1,450"


def not_found() -> discord.NotFound:
    return discord.NotFound(SimpleNamespace(status=404, reason="gone"), "Unknown")  # type: ignore[arg-type]


class FakeMessage:
    def __init__(self, thread: "FakeThread", message_id: int, content: str, view: Any = None) -> None:
        self.thread, self.id, self.content, self.view = thread, message_id, content, view

    async def edit(self, **changes: Any) -> None:
        if self.id not in self.thread.messages:
            raise not_found()
        self.content = changes.get("content", self.content)
        if "view" in changes:
            self.view = changes["view"]

    async def delete(self) -> None:
        if self.thread.messages.pop(self.id, None) is None:
            raise not_found()


class FakeThread:
    def __init__(self, forum: "FakeForum", owner_id: int, name: str, content: str) -> None:
        self.parent, self.parent_id, self.guild = forum, forum.id, forum.guild
        self.id, self.owner_id, self.name = forum.next_id(), owner_id, name
        self.archived = self.locked = self.deleted = False
        self.applied_tags: list[Any] = []
        self.messages: dict[int, FakeMessage] = {}
        self.starter_message = FakeMessage(self, self.id, content)
        self.jump_url = f"https://discord.com/channels/1/{self.id}"
        forum.threads_by_id[self.id] = self

    async def send(self, content: str, **kwargs: Any) -> FakeMessage:
        message = FakeMessage(self, self.parent.next_id(), content, kwargs.get("view"))
        self.messages[message.id] = message
        return message

    def get_partial_message(self, message_id: int) -> FakeMessage:
        return self.messages.get(message_id) or FakeMessage(self, message_id, "")

    async def fetch_message(self, message_id: int) -> FakeMessage:
        return self.starter_message

    async def edit(self, **changes: Any) -> "FakeThread":
        for key in ("archived", "locked", "applied_tags"):
            if key in changes:
                setattr(self, key, changes[key])
        return self

    async def delete(self) -> None:
        self.deleted = True
        self.parent.threads_by_id.pop(self.id, None)

    # What the stall looks like now
    @property
    def control(self) -> FakeMessage:
        (message,) = self.messages.values()  # there's only ever one bot message under a stall
        return message

    @property
    def tag_names(self) -> list[str]:
        return [t.name for t in self.applied_tags]


class FakeForum(discord.ForumChannel):
    def __init__(self) -> None:  # skips discord.py's setup: only what the bot uses
        self.id, self.name = 10, "stalls"
        self.guild = SimpleNamespace(id=1)  # type: ignore[assignment]
        self.threads_by_id: dict[int, FakeThread] = {}
        self._next = 1000
        self._tags = [SimpleNamespace(id=n, name=name) for n, name in enumerate(SETTINGS.tags.values(), start=1)]
        self._tags.append(SimpleNamespace(id=50, name="Knives"))  # a tag members can pick themselves

    @property
    def available_tags(self) -> list:  # type: ignore[override]
        return self._tags

    @property
    def mention(self) -> str:  # type: ignore[override]
        return "#stalls"

    def next_id(self) -> int:
        self._next += 1
        return self._next


class FakeUser:
    def __init__(self) -> None:
        self.dms: list[str] = []

    async def send(self, content: str, **_: Any) -> None:
        self.dms.append(content)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    forum = FakeForum()
    users: dict[int, FakeUser] = {}

    async def fetch_thread(guild: Any, thread_id: int) -> FakeThread:
        if thread_id not in forum.threads_by_id:
            raise not_found()
        return forum.threads_by_id[thread_id]

    monkeypatch.setattr(service, "fetch_thread", fetch_thread)
    monkeypatch.setattr(cog_module, "fetch_thread", fetch_thread)

    def user(user_id: int) -> FakeUser:
        return users.setdefault(user_id, FakeUser())

    bot = SimpleNamespace(
        get_guild=lambda _id: forum.guild,
        get_user=user,
        user=SimpleNamespace(id=BOT_ID),
        intents=SimpleNamespace(message_content=True),
    )
    return SimpleNamespace(forum=forum, bot=bot, user=user, db_path=tmp_path / "test.db")


def run(world: Any, body: Callable[[Any, StallStorage, Database], Awaitable[None]]) -> None:
    """Run a scenario with a fresh database and the stalls channel set up, always closing the database."""

    async def scenario() -> None:
        db = Database(world.db_path)
        await db.connect()
        try:
            storage = StallStorage(db)
            await storage.migrate()
            await storage.save_channel(StallChannel(10, 1, None))
            ctx = StallContext(bot=world.bot, storage=storage, settings=SETTINGS)  # type: ignore[arg-type]
            await body(ctx, storage, db)
        finally:
            await db.close()

    asyncio.run(scenario())


def edit_event(thread: FakeThread, content: str, edited_at: str) -> Any:
    return SimpleNamespace(
        message_id=thread.id, channel_id=thread.id, data={"content": content, "edited_timestamp": edited_at}
    )


async def turn_back_bump_clock(db: Database, hours: float) -> None:
    await db.execute("UPDATE stalls SET bumped_at = bumped_at - ?", int(hours * HOUR))


def test_a_stall_from_post_to_reopen(world: Any) -> None:
    async def body(ctx: StallContext, storage: StallStorage, db: Database) -> None:
        events = Stalls(ctx)

        async def fresh(thread: FakeThread) -> Any:
            current = await storage.get_stall(thread.id)
            assert current is not None
            return current

        # A member posts: it becomes their stall, with the Open tag and the buttons underneath.
        thread = FakeThread(world.forum, 100, "[Store] Doppler knives", GOOD_POST)
        thread.applied_tags = [world.forum.available_tags[2]]  # they picked "Knives" themselves
        await events.on_thread_create(thread)
        stall = await fresh(thread)
        assert stall.status == OPEN and stall.owner_id == 100
        assert thread.tag_names == ["Open", "Knives"]  # their own tag is kept
        assert thread.control.content.startswith("-# Stall of <@100> · last bumped <t:")
        assert isinstance(thread.control.view, ControlView)

        # An edit right away doesn't bump (the 6 hour cooldown) but counts as activity.
        first = thread.control.id
        await events.on_raw_message_edit(edit_event(thread, GOOD_POST + "\nAWP Asiimov - $80", "t1"))
        assert thread.control.id == first
        # The same edit event twice (Discord resends it when a link preview loads) is ignored.
        await turn_back_bump_clock(db, 7)
        await events.on_raw_message_edit(edit_event(thread, GOOD_POST, "t1"))
        assert thread.control.id == first
        # A later edit after 6 hours bumps: the control message is posted again and the old one deleted.
        await events.on_raw_message_edit(edit_event(thread, GOOD_POST, "t2"))
        assert thread.control.id > first and first not in thread.messages

        # The Bump button waits 12 hours.
        assert await service.bump(ctx, await fresh(thread), hours=12) > 0
        await turn_back_bump_clock(db, 13)
        before = thread.control.id
        assert await service.bump(ctx, await fresh(thread), hours=12) == 0
        assert thread.control.id > before

        # Close: the buttons become a note and a Reopen button, then the post is tagged, locked and hidden.
        await service.close_stall(ctx, await fresh(thread), "The owner closed it and can reopen it any time.")
        assert thread.control.content.startswith("⚫ **This stall is closed.** The owner closed it")
        assert isinstance(thread.control.view, ReopenView)
        assert thread.locked and thread.archived and thread.tag_names == ["Closed", "Knives"]
        assert (await fresh(thread)).status == CLOSED
        # Discord's "archived" event for that doesn't unhide it, because it's closed.
        await events.on_thread_update(SimpleNamespace(archived=False), thread)
        assert thread.archived

        # Reopen soon after a bump: visible and unlocked with its buttons back, but in the same spot.
        closed_note = thread.control.id
        await service.reopen_stall(ctx, await fresh(thread))
        assert not thread.locked and not thread.archived and thread.tag_names == ["Open", "Knives"]
        assert thread.control.id == closed_note and isinstance(thread.control.view, ControlView)
        # Reopen after the bump wait: back at the top (a new message).
        await service.close_stall(ctx, await fresh(thread), "Closed.")
        await turn_back_bump_clock(db, 13)
        await service.reopen_stall(ctx, await fresh(thread))
        assert thread.control.id > closed_note and isinstance(thread.control.view, ControlView)

        # A staff close is remembered, so only staff can reopen it; reopening clears that.
        await service.close_stall(ctx, await fresh(thread), "Closed by staff.", by_staff=True)
        assert (await fresh(thread)).closed_by_staff
        await service.reopen_stall(ctx, await fresh(thread))
        assert not (await fresh(thread)).closed_by_staff

    run(world, body)


def test_rule_breaking_posts_are_removed_with_a_copy(world: Any) -> None:
    async def body(ctx: StallContext, storage: StallStorage, db: Database) -> None:
        events = Stalls(ctx)
        first = FakeThread(world.forum, 100, "[Store] Knives", GOOD_POST)
        await events.on_thread_create(first)

        # A second post by the same member is removed; they're told where their stall is.
        second = FakeThread(world.forum, 100, "[Store] More knives", "my second shop")
        await events.on_thread_create(second)
        assert second.deleted and not first.deleted
        intro, copy = world.user(100).dms
        assert "You already have a stall: " + first.jump_url in intro
        assert copy == "```\nmy second shop\n```"

        # A post with a link outside CSFloat and Steam is removed.
        scam = FakeThread(world.forum, 200, "[Store] Cheap", "trade here https://steamcommunlty.com/tradeoffer/x")
        await events.on_thread_create(scam)
        assert scam.deleted and await storage.stall_of(1, 200) is None
        assert "<https://steamcommunlty.com/tradeoffer/x>" in world.user(200).dms[0]

        # Editing a bad link into an existing stall removes it too, which frees the member to post again.
        await events.on_raw_message_edit(edit_event(first, GOOD_POST + " https://evil.site", "t9"))
        assert first.deleted and (await storage.stall_of(1, 100)).is_deleted  # type: ignore[union-attr]
        await turn_back_bump_clock(db, 13)  # once the bump wait has passed, they can post again
        again = FakeThread(world.forum, 100, "[Store] Knives v2", GOOD_POST)
        await events.on_thread_create(again)
        assert not again.deleted and (await storage.stall_of(1, 100)).thread_id == again.id  # type: ignore[union-attr]

        # The bot's own Start here post is left alone.
        panel = FakeThread(world.forum, BOT_ID, "Start here: Open your stall", "")
        await events.on_thread_create(panel)
        assert not panel.deleted and await storage.get_stall(panel.id) is None

    run(world, body)


def test_upkeep_closes_quiet_stalls_unhides_open_ones_and_forgets_deleted(world: Any) -> None:
    async def body(ctx: StallContext, storage: StallStorage, db: Database) -> None:
        events = Stalls(ctx)
        quiet = FakeThread(world.forum, 100, "[Store] Old", GOOD_POST)
        hidden = FakeThread(world.forum, 200, "[Store] Busy", GOOD_POST)
        gone = FakeThread(world.forum, 300, "[Store] Gone", GOOD_POST)
        for thread in (quiet, hidden, gone):
            await events.on_thread_create(thread)

        await db.execute("UPDATE stalls SET active_at = active_at - ? WHERE thread_id = ?", 31 * 86400, quiet.id)
        hidden.archived = True  # Discord hid it after a week without messages
        world.forum.threads_by_id.pop(gone.id)  # deleted while the bot was offline
        await events.upkeep.coro(events)

        assert (await storage.get_stall(quiet.id)).status == CLOSED  # type: ignore[union-attr]
        assert "No edits or bumps for 30 days" in quiet.control.content and quiet.archived
        assert not hidden.archived and (await storage.get_stall(hidden.id)).status == OPEN  # type: ignore[union-attr]
        assert (await storage.get_stall(gone.id)).is_deleted  # type: ignore[union-attr]  # owner can post again later

    run(world, body)


def test_deleting_and_reposting_or_closing_and_reopening_cant_skip_the_bump_wait(world: Any) -> None:
    async def body(ctx: StallContext, storage: StallStorage, db: Database) -> None:
        events = Stalls(ctx)
        stall_post = FakeThread(world.forum, 100, "[Store] Knives", GOOD_POST)
        await events.on_thread_create(stall_post)

        # Close and reopen right away: it's open again, but stays where it was (same message, no new one).
        stall = await storage.get_stall(stall_post.id)
        await service.close_stall(ctx, stall, "Closed.")  # type: ignore[arg-type]
        note = stall_post.control.id
        await service.reopen_stall(ctx, await storage.get_stall(stall_post.id))  # type: ignore[arg-type]
        assert stall_post.control.id == note and isinstance(stall_post.control.view, ControlView)
        assert not stall_post.archived and (await storage.get_stall(stall_post.id)).status == OPEN  # type: ignore[union-attr]

        # The owner deletes their post and posts again straight away: the new one is removed, with a time.
        await stall_post.delete()
        await events.on_raw_thread_delete(SimpleNamespace(thread_id=stall_post.id))
        repost = FakeThread(world.forum, 100, "[Store] Knives again", GOOD_POST)
        await events.on_thread_create(repost)
        assert repost.deleted
        assert "You can post a new one <t:" in world.user(100).dms[0] and "(12 hours)" in world.user(100).dms[0]

        # After the wait, the new post becomes their stall.
        await turn_back_bump_clock(db, 13)
        later = FakeThread(world.forum, 100, "[Store] Knives, later", GOOD_POST)
        await events.on_thread_create(later)
        assert not later.deleted and (await storage.stall_of(1, 100)).thread_id == later.id  # type: ignore[union-attr]

        # Deleting just the post's text removes the whole post (an empty stall isn't a stall), same wait after.
        await events.on_raw_message_delete(SimpleNamespace(message_id=later.id, channel_id=later.id))
        assert later.deleted and (await storage.stall_of(1, 100)).is_deleted  # type: ignore[union-attr]

    run(world, body)
