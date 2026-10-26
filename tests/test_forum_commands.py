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
    expected = [{"name": t.name, "value": t.id} for t in TEMPLATES.values()]
    for name in ("setup", "testpost", "remove"):
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
    assert [c["value"] for c in options(small, "setup")["template"]["choices"]] == ["bug_report"]
    assert len(options(full, "setup")["template"]["choices"]) == len(TEMPLATES)
