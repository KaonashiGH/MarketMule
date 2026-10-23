"""Small Discord helpers shared by every feature."""

from __future__ import annotations

import re

import discord


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
