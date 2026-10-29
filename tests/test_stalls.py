"""Stalls: settings, link checks, timing, message text, storage, buttons and commands."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from marketmule.core.config import PROJECT_ROOT
from marketmule.core.database import Database
from marketmule.features.stalls.cog import Stalls
from marketmule.features.stalls.render import (
    DAY,
    HOUR,
    MESSAGE_LIMIT,
    bad_links,
    control_text,
    find_links,
    is_allowed,
    is_inactive,
    link_host,
    removal_message,
    template_message,
    wait_left,
)
from marketmule.features.stalls.settings import SettingsError, load_settings, validate
from marketmule.features.stalls.storage import CLOSED, Stall, StallStorage
from marketmule.features.stalls.views import (
    BUMP_BUTTON_ID,
    CLOSE_BUTTON_ID,
    REOPEN_BUTTON_ID,
    TEMPLATE_BUTTON_ID,
    ControlView,
    ReopenView,
    StallPanel,
)

SETTINGS_PATH = PROJECT_ROOT / "templates" / "stalls" / "stalls.json"
SETTINGS = load_settings(SETTINGS_PATH)
HOSTS = SETTINGS.allowed_link_hosts
NOW = 1_790_000_000


def stall(**changes: Any) -> Stall:
    base = Stall(thread_id=1, guild_id=1, channel_id=10, owner_id=100, created_at=NOW, active_at=NOW, bumped_at=NOW)
    return replace(base, **changes)


# Settings


def test_shipped_settings_are_valid() -> None:
    assert validate(SETTINGS) == []
    assert HOSTS == ("csfloat.com", "steamcommunity.com")
    assert SETTINGS.tags == {"open": "Open", "closed": "Closed"}


def test_settings_catch_mistakes(tmp_path: Path) -> None:
    raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    raw["allowed_link_hosts"] = ["https://csfloat.com/"]
    raw["control"]["text"] = "Stall of someone"  # lost its {owner} and {bumped}
    raw["template"] = "x" * 2000
    path = tmp_path / "stalls.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SettingsError) as error:
        load_settings(path)
    message = str(error.value)
    assert "plain site name" in message and "{owner}" in message and "template" in message


# Links


def test_finds_the_links_discord_makes_clickable() -> None:
    text = (
        "Store: https://csfloat.com/stall/123. Trade: <https://steamcommunity.com/tradeoffer/new/?partner=1&token=x>\n"
        "[my store](https://csfloat.com/stall/9) and www.example.com, join discord.gg/abc123"
    )
    assert find_links(text) == [
        "https://csfloat.com/stall/123",
        "https://steamcommunity.com/tradeoffer/new/?partner=1&token=x",
        "https://csfloat.com/stall/9",
        "www.example.com",
        "discord.gg/abc123",
    ]


def test_only_csfloat_and_steam_links_are_allowed() -> None:
    good = [
        "https://csfloat.com/stall/123",
        "https://CSFloat.com/item/9",
        "https://www.csfloat.com/",
        "https://steamcommunity.com/tradeoffer/new/?partner=1&token=x",
        "http://steamcommunity.com/id/someone",
    ]
    bad = [
        "https://csfloat.com.evil.site/stall/1",  # look-alike: the real site is evil.site
        "https://csfloat.com@evil.site/",  # everything before @ is ignored by browsers
        "https://csfIoat.com/stall/1",  # capital i instead of l
        "https://steamcommunlty.com/tradeoffer/new/",
        "https://notcsfloat.com/",
        "www.example.com",
        "discord.gg/abc123",
    ]
    assert all(is_allowed(link, HOSTS) for link in good)
    assert not any(is_allowed(link, HOSTS) for link in bad)
    assert link_host("https://csfloat.com@evil.site/") == "evil.site"


def test_a_post_with_only_good_links_passes() -> None:
    assert bad_links("Trade me https://steamcommunity.com/tradeoffer/new/?partner=1&token=x", HOSTS) == []
    assert bad_links("no links at all, just **text**", HOSTS) == []
    assert bad_links("cheap knives https://csfloat.co/x", HOSTS) == ["https://csfloat.co/x"]


# Timing


def test_cooldowns_and_inactivity() -> None:
    assert wait_left(NOW - 13 * HOUR, NOW, 12) == 0
    assert wait_left(NOW - 2 * HOUR, NOW, 12) == 10 * HOUR
    assert wait_left(NOW, NOW, 0) == 0
    assert is_inactive(stall(active_at=NOW - 30 * DAY), NOW, 30)
    assert not is_inactive(stall(active_at=NOW - 29 * DAY), NOW, 30)
    assert not is_inactive(stall(active_at=0, status=CLOSED), NOW, 30)


# Message text


def test_control_message() -> None:
    assert control_text(SETTINGS, stall()) == (
        f"-# Stall of <@100> · last bumped <t:{NOW}:R>\n"
        "-# Owner: **Bump** moves your stall to the top (every 12 hours). "
        "Editing your post bumps it too (at most every 6 hours)."
    )


def test_template_fits_and_is_copyable() -> None:
    message = template_message(SETTINGS)
    assert len(message) <= MESSAGE_LIMIT
    assert message.count("```") == 2 and "🏪 **WELCOME TO MY STALL**" in message
    assert bad_links(SETTINGS.template, HOSTS) == []  # the template's own example links are allowed


def test_removal_dm_keeps_what_they_wrote() -> None:
    long = "A" * 3000 + "```"
    first, copy = removal_message(SETTINGS, title="[Store] Knives", channel="#stalls", reason="Bad link.", content=long)
    assert first.startswith("Your post **[Store] Knives** in #stalls was removed. Bad link.")
    assert len(first) <= MESSAGE_LIMIT and len(copy) <= MESSAGE_LIMIT
    assert copy.startswith("```\nAAA") and copy.endswith("\n```") and copy.count("```") == 2
    (only,) = removal_message(SETTINGS, title="x", channel="#s", reason="r", content="  ")


# Storage


def test_storage_one_stall_per_member(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        try:
            storage = StallStorage(db)
            await storage.migrate()
            await storage.add_stall(stall(control_message_id=5, closed_by_staff=True))
            with pytest.raises(sqlite3.IntegrityError):
                await storage.add_stall(stall(thread_id=2))  # same member, second stall
            saved = await storage.stall_of(1, 100)
            assert saved is not None and saved.control_message_id == 5 and saved.closed_by_staff
            saved.status = CLOSED
            await storage.save_stall(saved)
            assert await storage.count_stalls(10) == (0, 1)
            assert await storage.open_stalls() == []
            await storage.mark_deleted(1)
            assert (await storage.stall_of(1, 100)).is_deleted  # type: ignore[union-attr]
            assert await storage.count_stalls(10) == (0, 0)  # deleted ones aren't counted
            await storage.delete_stall(1)
            assert await storage.stall_of(1, 100) is None
        finally:
            await db.close()

    asyncio.run(scenario())


# Buttons and commands

CTX = cast(Any, SimpleNamespace(bot=None, settings=SETTINGS))


def test_buttons_survive_restarts() -> None:
    async def build() -> list[Any]:
        return [StallPanel(CTX), ControlView(CTX), ReopenView(CTX)]

    views = asyncio.run(build())
    assert all(view.is_persistent() for view in views)
    ids = [c.custom_id for v in views for c in v.walk_children() if isinstance(c, discord.ui.Button)]
    assert ids == [TEMPLATE_BUTTON_ID, BUMP_BUTTON_ID, CLOSE_BUTTON_ID, REOPEN_BUTTON_ID]


def test_commands() -> None:
    cog = Stalls(CTX)
    assert cog.__cog_group_name__ == "stalls"
    # Setup and removal are in /forums setup and /forums remove (pick "Stalls"); /stalls keeps the staff tools.
    assert {c.name for c in cog.walk_app_commands()} == {"close", "reopen", "list"}
    assert hasattr(cog, "setup_channel") and hasattr(cog, "remove_channel")


def forum_with(require_tag: bool, member_tags: bool) -> Any:
    perms = SimpleNamespace(send_messages=True, send_messages_in_threads=False)  # the right setup
    tags = [SimpleNamespace(id=1, name="Open", moderated=True), SimpleNamespace(id=2, name="Closed", moderated=True)]
    if member_tags:
        tags.append(SimpleNamespace(id=3, name="Knives", moderated=False))
    return SimpleNamespace(
        guild=SimpleNamespace(default_role="everyone"),
        permissions_for=lambda _role: perms,
        flags=SimpleNamespace(require_tag=require_tag),
        available_tags=tags,
        threads=[],
    )


def test_setup_warns_when_members_could_not_post() -> None:
    from marketmule.features.stalls.service import setup_channel_problems

    assert asyncio.run(setup_channel_problems(forum_with(require_tag=False, member_tags=False))) == []
    assert asyncio.run(setup_channel_problems(forum_with(require_tag=True, member_tags=True))) == []
    (problem,) = asyncio.run(setup_channel_problems(forum_with(require_tag=True, member_tags=False)))
    assert "Require people to select tags" in problem


def test_start_here_post_gets_a_tag_when_the_channel_requires_one() -> None:
    sent: list[Any] = []

    async def scenario(require_tag: bool) -> None:
        forum = forum_with(require_tag=require_tag, member_tags=True)
        thread = SimpleNamespace(id=5)

        async def edit(**_: Any) -> Any:
            return thread

        thread.edit = edit

        async def create_thread(**kwargs: Any) -> Any:
            sent.append(kwargs["applied_tags"])
            return SimpleNamespace(thread=thread)

        forum.create_thread = create_thread
        ctx = cast(Any, SimpleNamespace(bot=SimpleNamespace(config=None), settings=SETTINGS))
        await Stalls(ctx)._post_panel(forum)

    asyncio.run(scenario(require_tag=True))
    asyncio.run(scenario(require_tag=False))
    assert [[t.name for t in tags] for tags in sent] == [["Open"], []]
