"""Forum tag helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import discord

from .templates import MAX_FORUM_TAGS, Template

AUDIT_REASON = "MarketMule forum setup"


class TagLimitError(Exception):
    def __init__(self, needed: int) -> None:
        super().__init__(f"Forum would need {needed} tags; Discord allows {MAX_FORUM_TAGS}.")
        self.needed = needed


def find_tag(forum: discord.ForumChannel, name: str) -> discord.ForumTag | None:
    name = name.lower()
    return next((tag for tag in forum.available_tags if tag.name.lower() == name), None)


def status_first(tags: Sequence[discord.ForumTag], statuses: Iterable[str]) -> list[discord.ForumTag]:
    """Put status tags in front, keeping everything else in order. The status should always be the first tag."""
    names = {name.lower() for name in statuses}
    return sorted(tags, key=lambda tag: tag.name.lower() not in names)  # sorted() keeps ties in order


def find_tags(forum: discord.ForumChannel, names: Iterable[str]) -> list[discord.ForumTag]:
    tags: list[discord.ForumTag] = []
    for name in names:
        tag = find_tag(forum, name)
        if tag is not None and tag not in tags:
            tags.append(tag)
    return tags


async def sync_forum_tags(forum: discord.ForumChannel, templates: Sequence[Template]) -> int:
    """Create every tag the channel's forms need and apply their tag emojis. Returns the number created.

    Forms sharing a channel share its 20-tag limit, so the check covers all of them together.
    Discord doesn't support colored tags, so templates use colored emojis instead.
    """
    existing = {tag.name.lower() for tag in forum.available_tags}
    missing: list[tuple[str, bool]] = []
    for template in templates:
        for name, moderated in template.forum_tags():
            if name.lower() not in existing:
                existing.add(name.lower())
                missing.append((name, moderated))
    #create form status tags first before sorting/type tags 
    missing.sort(key=lambda tag: not tag [1]) #moderated status first -> sort -> keep rest in order of oldest first 
    needed = len(forum.available_tags) + len(missing)
    if needed > MAX_FORUM_TAGS:
        raise TagLimitError(needed)

    emojis = {name.lower(): emoji for template in templates for name, emoji in template.tag_emojis.items()}
    for name, moderated in missing:
        emoji = emojis.get(name.lower())
        await forum.create_tag(
            name=name,
            moderated=moderated,
            emoji=discord.PartialEmoji(name=emoji) if emoji else None,
            reason=AUDIT_REASON,
        )

    refreshed = await forum.guild.fetch_channel(forum.id)
    if not isinstance(refreshed, discord.ForumChannel):
        raise RuntimeError(f"Channel {forum.id} is no longer a forum")

    # Status tags go first in the forum's list too, so Discord shows them first on every post.
    statuses = [status for template in templates for status in template.statuses]
    tags = status_first(refreshed.available_tags, statuses)
    changed = tags != list(refreshed.available_tags)
    for tag in tags:
        emoji = emojis.get(tag.name.lower())
        if emoji and str(tag.emoji or "") != emoji:
            tag.emoji = discord.PartialEmoji(name=emoji)
            changed = True
    if changed:
        await refreshed.edit(available_tags=tags, reason=AUDIT_REASON)

    return len(missing)


async def set_thread_tags(thread: discord.Thread, tags: Sequence[discord.ForumTag]) -> discord.Thread:
    """Replace a post's tags, temporarily reopening it if it's archived."""
    archived = thread.archived
    if archived:
        thread = await thread.edit(archived=False)
    thread = await thread.edit(applied_tags=list(tags))
    if archived:
        thread = await thread.edit(archived=True)
    return thread
