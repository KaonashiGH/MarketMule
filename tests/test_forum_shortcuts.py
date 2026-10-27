"""Staff shortcuts such as /nab: the template settings, the slash commands, and what happens in the post."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums import shortcuts, status
from marketmule.features.forums.shortcuts import build_shortcut_commands, run_shortcut, support_button
from marketmule.features.forums.storage import Post
from marketmule.features.forums.templates import StaffShortcut, _validate, load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")
BUG = TEMPLATES["bug_report"]
REPORTER = 555


def nab() -> StaffShortcut:
    shortcut = BUG.shortcut("nab")
    assert shortcut is not None
    return shortcut


# --- Settings -------------------------------------------------------------------------------------


def test_bug_reports_have_nab() -> None:
    shortcut = nab()
    assert shortcut.status == "Not A Bug"
    assert shortcut.status in BUG.final_statuses  # so /nab closes the post
    assert "{user}" in shortcut.message
    assert shortcut.button_url == "https://csfloat.com/support"


def test_other_forms_have_no_nab() -> None:
    assert all(t.shortcut("nab") is None for t in TEMPLATES.values() if t.id != "bug_report")


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"command": "NAB"}, "lowercase"),
        ({"command": "not a bug"}, "lowercase"),
        ({"status": "Denied"}, "not listed in statuses"),
        ({"description": ""}, "description"),
        ({"message": "{user} " + "x" * 1985}, "message"),  # fits as typed, too long once {user} is a ping
        ({"button_url": "http://csfloat.com/support"}, "https://"),
        ({"button_label": None}, "go together"),
    ],
)
def test_broken_shortcuts_are_caught(change: dict, problem: str) -> None:
    broken = replace(BUG, staff_shortcuts=(replace(nab(), **change),))
    assert any(problem in error for error in _validate(broken)), _validate(broken)


def test_duplicate_shortcuts_are_caught() -> None:
    broken = replace(BUG, staff_shortcuts=(nab(), nab()))
    assert any("listed twice" in error for error in _validate(broken))


# --- The slash command ----------------------------------------------------------------------------


def test_nab_is_a_hidden_staff_command() -> None:
    commands = build_shortcut_commands(cast(Any, SimpleNamespace(templates=TEMPLATES)))
    assert [c.name for c in commands] == ["nab"]
    tree = discord.app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    payload = commands[0].to_dict(tree)
    assert payload["description"] == nab().description
    assert payload["default_member_permissions"] == discord.Permissions(manage_threads=True).value
    assert payload["dm_permission"] is False  # servers only
    assert payload["options"] == []


def test_support_button_is_a_link() -> None:
    view = support_button(nab())
    assert view is not None
    [button] = view.children
    assert isinstance(button, discord.ui.Button)
    assert button.url == "https://csfloat.com/support"
    assert button.label == "Open a support ticket"


# --- Using it in a post ---------------------------------------------------------------------------


class FakeMember(discord.Member):
    def __init__(self, member_id: int) -> None:
        self.fake_id = member_id
        self.fake_guild = SimpleNamespace(id=1)

    @property
    def id(self) -> int:  # type: ignore[override]
        return self.fake_id

    @property
    def guild(self) -> Any:  # type: ignore[override]
        return self.fake_guild

    @property
    def mention(self) -> str:  # type: ignore[override]
        return f"<@{self.fake_id}>"

    def __str__(self) -> str:
        return f"member {self.fake_id}"


class FakeThread:
    def __init__(self) -> None:
        self.id = 99
        self.name = "[🔵 Open] - Account · Can't log in"
        self.parent = None
        self.applied_tags: list = []
        self.archived = False
        self.locked = False
        self.events: list[tuple[str, Any]] = []

    async def edit(self, **changes: Any) -> "FakeThread":
        self.events.append(("edit", changes))
        for key, value in changes.items():
            setattr(self, key, value)
        return self

    async def send(self, content: str, **options: Any) -> None:
        self.events.append(("send", content, options))


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.deferred = False

    async def send_message(self, content: str, **_: Any) -> None:
        self.messages.append(content)

    async def defer(self, **_: Any) -> None:
        self.deferred = True


class FakeStorage:
    def __init__(self, post: Post | None) -> None:
        self.post = post
        self.saved_status: str | None = None

    async def get_post(self, thread_id: int) -> Post | None:
        return self.post if self.post and self.post.thread_id == thread_id else None

    async def set_post_status(self, thread_id: int, new_status: str) -> None:
        self.saved_status = new_status


def make_setup(
    monkeypatch: pytest.MonkeyPatch, *, staff: bool = True, post_status: str = "Open", template="bug_report"
):
    thread = FakeThread()
    post = Post(thread_id=99, channel_id=10, template_id=template, number=4, reporter_id=REPORTER, status=post_status)
    storage = FakeStorage(post)
    forums = cast(Any, SimpleNamespace(bot=SimpleNamespace(db=None), storage=storage, templates=TEMPLATES))
    followups: list[str] = []
    interaction = cast(
        Any,
        SimpleNamespace(
            user=FakeMember(7),
            channel_id=99,
            response=FakeResponse(),
            followup=SimpleNamespace(send=lambda content, **_: _record(followups, content)),
        ),
    )

    async def fake_is_staff(_db: Any, _user: Any) -> bool:
        return staff

    async def fake_fetch_thread(_guild: Any, _thread_id: int) -> FakeThread:
        return thread

    monkeypatch.setattr(shortcuts, "is_staff", fake_is_staff)
    monkeypatch.setattr(status, "fetch_thread", fake_fetch_thread)
    return forums, interaction, thread, storage, followups


async def _record(log: list[str], content: str) -> None:
    log.append(content)


def test_nab_messages_the_poster_then_closes_the_post(monkeypatch: pytest.MonkeyPatch) -> None:
    forums, interaction, thread, storage, followups = make_setup(monkeypatch)
    asyncio.run(run_shortcut(forums, interaction, "nab"))

    sends = [e for e in thread.events if e[0] == "send"]
    assert len(sends) == 1  # the ready-made message only, not the usual "set the status to"
    _, content, options = sends[0]
    assert content.startswith(f"Hi <@{REPORTER}>, thanks for the report.")
    assert "https://csfloat.com/support" in content
    assert options["view"].children[0].url == "https://csfloat.com/support"
    assert [u.id for u in options["allowed_mentions"].users] == [REPORTER]  # pings only the poster

    # Order: retitle, message, then lock, so the message is the last thing in the post.
    kinds = [e[0] for e in thread.events]
    assert kinds.index("send") < max(i for i, e in enumerate(thread.events) if e[0] == "edit" and e[1].get("locked"))
    assert thread.name == "[❌ Not A Bug] - Account · Can't log in"
    assert thread.locked and thread.archived
    assert storage.saved_status == "Not A Bug"
    assert followups == ["Status set to **Not A Bug** and the post is now locked."]


def test_members_cannot_use_nab(monkeypatch: pytest.MonkeyPatch) -> None:
    forums, interaction, thread, storage, _ = make_setup(monkeypatch, staff=False)
    asyncio.run(run_shortcut(forums, interaction, "nab"))
    assert interaction.response.messages == ["Only staff can use /nab."]
    assert thread.events == [] and storage.saved_status is None


def test_nab_outside_a_bug_report(monkeypatch: pytest.MonkeyPatch) -> None:
    forums, interaction, thread, _, _ = make_setup(monkeypatch, template="price_check")
    asyncio.run(run_shortcut(forums, interaction, "nab"))
    assert interaction.response.messages == ["/nab isn't available for price check posts."]

    forums.storage.post = None
    asyncio.run(run_shortcut(forums, interaction, "nab"))
    assert interaction.response.messages[-1] == "Use /nab inside a post created by MarketMule."
    assert thread.events == []


def test_nab_twice_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    forums, interaction, thread, _, _ = make_setup(monkeypatch, post_status="Not A Bug")
    asyncio.run(run_shortcut(forums, interaction, "nab"))
    assert interaction.response.messages == ["This post is already marked **Not A Bug**."]
    assert thread.events == []


def test_manage_post_message_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an announcement, set_status still posts its usual message."""
    forums, interaction, thread, _, _ = make_setup(monkeypatch)
    post = forums.storage.post
    asyncio.run(status.set_status(forums, post, BUG, interaction.user, "Fixed"))
    sends = [e for e in thread.events if e[0] == "send"]
    assert sends[0][1] == f"<@7> set the status to **Fixed**. <@{REPORTER}>"
