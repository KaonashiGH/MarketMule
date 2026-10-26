"""Small Discord helpers shared by every feature."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable

import discord
from discord import app_commands
from discord.ext import commands

# Discord shows at most 25 fixed choices per option.
MAX_CHOICES = 25


def set_option(cog: commands.Cog, command: str, option: str, **changes: object) -> None:
    """Change one slash-command option on this cog after its settings are known (at startup).

    Used to give an option a fixed list of choices (instant, unlike autocomplete) or to limit a
    channel option to certain channel types. Must run before the cog is added to the bot."""
    for cmd in cog.walk_app_commands():
        if isinstance(cmd, app_commands.Command) and cmd.name == command:
            params = cmd._params  # discord.py has no public setter for this
            params[option] = dataclasses.replace(params[option], **changes)
            return
    raise LookupError(f"{type(cog).__name__} has no /{command} command")


def fixed_choices(pairs: Iterable[tuple[str, str]]) -> list[app_commands.Choice[str]]:
    """(name shown, value sent) pairs as Discord choices, cut to Discord's limit of 25."""
    return [app_commands.Choice(name=name[:100], value=value) for name, value in pairs][:MAX_CHOICES]


FORUM_ONLY = [discord.ChannelType.forum]


def webhook_username(display_name: str) -> str:
    """Discord rejects webhook names that contain 'discord' or 'clyde'."""
    cleaned = re.sub(r"(?i)discord|clyde", "", display_name).strip()
    return (cleaned or "Member")[:80]


async def fetch_thread(guild: discord.Guild, thread_id: int) -> discord.Thread:
    thread = guild.get_thread(thread_id) or await guild.fetch_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        raise RuntimeError(f"Channel {thread_id} is not a thread")
    return thread


async def fetch_forum(guild: discord.Guild, channel_id: int) -> discord.ForumChannel:
    channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    if not isinstance(channel, discord.ForumChannel):
        raise RuntimeError(f"Channel {channel_id} is not a forum")
    return channel
