"""The slash-command options Discord receives: real forum pickers and fixed form lists, no autocomplete."""

from types import SimpleNamespace
from typing import Any, cast

import discord

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.admin import ForumAdmin
from marketmule.features.forums.ratings import Leaderboard
from marketmule.features.forums.templates import load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")
FORUMS = cast(Any, SimpleNamespace(templates=TEMPLATES))


def options(cog: Any, name: str) -> dict[str, dict]:
    for command in cog.walk_app_commands():
        if command.name == name and isinstance(command, discord.app_commands.Command):
            return {p.display_name: p.to_dict() for p in command._params.values()}
    raise AssertionError(f"no /{name}")


def test_channel_option_is_a_forum_only_picker() -> None:
    admin = ForumAdmin(FORUMS)
    for name in ("setup", "remove"):
        channel = options(admin, name)["channel"]
        assert channel["type"] == discord.AppCommandOptionType.channel.value
        assert channel["channel_types"] == [discord.ChannelType.forum.value]
        assert "autocomplete" not in channel


def test_form_options_are_fixed_lists() -> None:
    admin = ForumAdmin(FORUMS)
    forms = [{"name": t.name, "value": t.id} for t in TEMPLATES.values()]
    stalls = {"name": "Stalls", "value": "stalls"}  # setup and remove also set up the stalls channel
    for name, expected in (("setup", [*forms, stalls]), ("remove", [*forms, stalls]), ("testpost", forms)):
        template = options(admin, name)["template"]
        assert "autocomplete" not in template
        assert [{"name": c["name"], "value": c["value"]} for c in template["choices"]] == expected
    assert options(admin, "remove")["template"]["required"] is False


def test_leaderboard_lists_only_rated_forms() -> None:
    form = options(Leaderboard(FORUMS), "leaderboard")["form"]
    assert "autocomplete" not in form
    rated = [t.id for t in TEMPLATES.values() if t.rating is not None]
    assert [c["value"] for c in form.get("choices", [])] == rated


def test_each_bot_gets_its_own_lists() -> None:
    """Changing the options on one cog must not leak into another (or into the class)."""
    only_bugs = cast(Any, SimpleNamespace(templates={"bug_report": TEMPLATES["bug_report"]}))
    small, full = ForumAdmin(only_bugs), ForumAdmin(FORUMS)
    assert [c["value"] for c in options(small, "setup")["template"]["choices"]] == ["bug_report", "stalls"]
    assert len(options(full, "setup")["template"]["choices"]) == len(TEMPLATES) + 1


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, content: str, **_: Any) -> None:
        self.messages.append(content)


class FakeStalls:
    """Stands in for the stalls feature's cog, which /forums finds through the bot."""

    def __init__(self, stalls_channel: int) -> None:
        self.stalls_channel = stalls_channel
        self.calls: list[str] = []

    async def is_stalls_channel(self, channel_id: int) -> bool:
        return channel_id == self.stalls_channel

    async def setup_channel(self, interaction: Any, channel: Any) -> None:
        self.calls.append(f"setup {channel.id}")

    async def remove_channel(self, interaction: Any, channel: Any) -> None:
        self.calls.append(f"remove {channel.id}")


def test_stalls_are_set_up_and_removed_through_forums_commands() -> None:
    import asyncio

    stalls = FakeStalls(stalls_channel=20)

    async def no_binding(_channel_id: int) -> None:
        return None

    forums = cast(
        Any,
        SimpleNamespace(
            templates=TEMPLATES,
            bot=SimpleNamespace(get_cog=lambda name: stalls if name == "Stalls" else None),
            storage=SimpleNamespace(get_binding=no_binding),
        ),
    )
    admin = ForumAdmin(forums)
    setup = next(c for c in admin.walk_app_commands() if c.name == "setup")
    remove = next(c for c in admin.walk_app_commands() if c.name == "remove")

    def interaction() -> Any:
        return SimpleNamespace(guild=SimpleNamespace(id=1), response=FakeResponse())

    async def scenario() -> None:
        await setup.callback(admin, interaction(), SimpleNamespace(id=20, mention="#stalls"), "stalls")
        await remove.callback(admin, interaction(), SimpleNamespace(id=20, mention="#stalls"), None)
        # A form can't be added to the stalls channel.
        refused = interaction()
        await setup.callback(admin, refused, SimpleNamespace(id=20, mention="#stalls"), "bug_report")
        assert refused.response.messages == ["#stalls is the stalls channel. Use a separate forum channel for forms."]
        # "Stalls" picked for a channel that isn't the stalls channel.
        wrong = interaction()
        await remove.callback(admin, wrong, SimpleNamespace(id=30, mention="#bugs"), "stalls")
        assert wrong.response.messages == ["#bugs isn't the stalls channel."]

    asyncio.run(scenario())
    assert stalls.calls == ["setup 20", "remove 20"]
