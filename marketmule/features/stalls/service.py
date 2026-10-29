"""Doing things to stalls on Discord: take on a new post, remove a rule-breaking one, bump, close, reopen."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from marketmule.core.discord_utils import fetch_thread

from .render import bad_links, closed_text, control_text, hours_text, removal_message, wait_left
from .storage import CLOSED, DELETED, OPEN, Stall, StallChannel, now

if TYPE_CHECKING:
    from .context import StallContext

log = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()
WEEK_IN_MINUTES = 10080  # the longest Discord waits before hiding a quiet post; the bot unhides open stalls anyway
MAX_POST_TAGS = 5


class StallError(Exception):
    """Something the member should hear about. The message is shown to them."""


# Checking a member's post


async def starter_message(thread: discord.Thread, tries: int = 4) -> discord.Message | None:
    """The post's first message. Right after a post is made, Discord can take a moment to return it."""
    for attempt in range(tries):
        try:
            return thread.starter_message or await thread.fetch_message(thread.id)
        except discord.NotFound:
            if attempt < tries - 1:
                await asyncio.sleep(1 + attempt)
        except discord.HTTPException:
            return None
    return None


async def problem_with_new_post(ctx: StallContext, thread: discord.Thread, owner_id: int, content: str) -> str | None:
    """Why a new post can't be a stall, or None if it's fine.

    One stall per member; CSFloat and Steam links only; and a new post after deleting the old one counts
    as a bump, so it has to wait as long as the Bump button would (deleting and reposting can't skip it)."""
    existing = await ctx.storage.stall_of(thread.guild.id, owner_id)
    if existing is not None and existing.thread_id != thread.id:
        if not existing.is_deleted:
            try:
                old = await fetch_thread(thread.guild, existing.thread_id)
            except (discord.NotFound, RuntimeError):
                existing.status = DELETED  # gone while the bot wasn't looking
            else:
                return ctx.settings.removed.duplicate.format(link=old.jump_url)
        hours = ctx.settings.limits.manual_bump_hours
        wait = wait_left(existing.bumped_at, now(), hours)
        if wait:
            return ctx.settings.removed.too_soon.format(when=f"<t:{now() + wait}:R>", hours=hours_text(hours))
        await ctx.storage.delete_stall(existing.thread_id)  # waited long enough: the new post replaces it
    links = bad_links(content, ctx.settings.allowed_link_hosts)
    if links:
        return ctx.settings.removed.bad_link.format(links=", ".join(f"<{link}>" for link in links[:5]))
    return None


async def remove_post(ctx: StallContext, thread: discord.Thread, owner_id: int, reason: str, content: str) -> None:
    """DM the owner why (with a copy of what they wrote), then delete the post."""
    parent = thread.parent.mention if thread.parent else "the stalls channel"
    messages = removal_message(ctx.settings, title=thread.name, channel=parent, reason=reason, content=content)
    try:
        user = ctx.bot.get_user(owner_id) or await ctx.bot.fetch_user(owner_id)
        for text in messages:
            await user.send(text, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        log.info("Couldn't DM %s about their removed stall post (DMs closed?)", owner_id)
    try:
        await thread.delete()
    except discord.HTTPException:
        log.warning("Couldn't delete stall post %s", thread.id)
    log.info("Removed stall post %s by %s: %s", thread.id, owner_id, reason.splitlines()[0])


# Taking on a new stall


async def register_stall(ctx: StallContext, thread: discord.Thread, owner_id: int) -> Stall:
    """Turn a member's new post into a stall: Open tag, the control message underneath, and a database row."""
    moment = now()
    stall = Stall(
        thread_id=thread.id,
        guild_id=thread.guild.id,
        channel_id=thread.parent_id or 0,
        owner_id=owner_id,
        status=OPEN,
        created_at=moment,
        active_at=moment,
        bumped_at=moment,
    )
    await ctx.storage.add_stall(stall)
    try:
        thread = await thread.edit(auto_archive_duration=WEEK_IN_MINUTES)
    except discord.HTTPException:
        log.info("Couldn't set the archive time on %s", thread.name)
    async with ctx.lock(thread.id):
        await _set_status_tag(ctx, thread, OPEN)
        await _post_control(ctx, thread, stall)
        await ctx.storage.save_stall(stall)
    log.info("New stall %s by %s", thread.name, owner_id)
    return stall


# Bumping


async def bump(ctx: StallContext, stall: Stall, *, hours: float, by_owner: bool = True) -> int:
    """Move the stall to the top if `hours` have passed since the last bump. Returns 0, or seconds to wait."""
    async with ctx.lock(stall.thread_id):
        moment = now()
        wait = wait_left(stall.bumped_at, moment, hours)
        if wait:
            if by_owner:
                stall.active_at = moment
                await ctx.storage.save_stall(stall)
            return wait
        thread = await _thread(ctx, stall)
        if thread.archived:
            thread = await thread.edit(archived=False)
        stall.bumped_at = moment
        if by_owner:
            stall.active_at = moment
        await _post_control(ctx, thread, stall)
        await ctx.storage.save_stall(stall)
        return 0


async def post_edited(ctx: StallContext, stall: Stall) -> bool:
    """The owner edited their post: it counts as activity, and bumps at most every edit_bump_hours.

    Returns True if the stall was bumped."""
    return await bump(ctx, stall, hours=ctx.settings.limits.edit_bump_hours) == 0


async def _post_control(ctx: StallContext, thread: discord.Thread, stall: Stall) -> None:
    """Post the control message (a new message is what moves a forum post up), then delete the old one."""
    from .views import ControlView  # the views use this module, so import here

    old = stall.control_message_id
    sent = await thread.send(control_text(ctx.settings, stall), view=ControlView(ctx), allowed_mentions=NO_MENTIONS)
    stall.control_message_id = sent.id
    if old and old != sent.id:
        try:
            await thread.get_partial_message(old).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.warning("Couldn't delete the old control message in %s", thread.name)


# Closing and reopening


async def close_stall(ctx: StallContext, stall: Stall, reason: str, *, by_staff: bool = False) -> None:
    """Swap the buttons for a note and a Reopen button, tag it Closed, then lock and hide the post."""
    from .views import ReopenView

    async with ctx.lock(stall.thread_id):
        thread = await _thread(ctx, stall)
        if thread.archived:
            thread = await thread.edit(archived=False)
        text = closed_text(ctx.settings, reason)
        edited = False
        if stall.control_message_id is not None:
            try:
                await thread.get_partial_message(stall.control_message_id).edit(
                    content=text, view=ReopenView(ctx), allowed_mentions=NO_MENTIONS
                )
                edited = True
            except discord.NotFound:
                pass  # deleted by hand: post a new one below
        if not edited:
            sent = await thread.send(text, view=ReopenView(ctx), allowed_mentions=NO_MENTIONS)
            stall.control_message_id = sent.id
        await _set_status_tag(ctx, thread, CLOSED)
        stall.status = CLOSED
        stall.closed_by_staff = by_staff
        await ctx.storage.save_stall(stall)  # saved before archiving, so the "unhide open stalls" listener skips it
        try:
            await thread.edit(locked=True, archived=True)
        except discord.HTTPException:
            log.warning("Couldn't lock and archive %s", thread.name)
    log.info("Closed stall %s: %s", stall.thread_id, reason)


async def reopen_stall(ctx: StallContext, stall: Stall) -> discord.Thread:
    async with ctx.lock(stall.thread_id):
        thread = await _thread(ctx, stall)
        thread = await thread.edit(archived=False, locked=False)
        moment = now()
        stall.status = OPEN
        stall.closed_by_staff = False
        stall.active_at = moment
        await _set_status_tag(ctx, thread, OPEN)
        if wait_left(stall.bumped_at, moment, ctx.settings.limits.manual_bump_hours) == 0:
            stall.bumped_at = moment
            await _post_control(ctx, thread, stall)  # a new message: back at the top
        else:
            await _restore_control(ctx, thread, stall)  # same message, same spot: closing can't skip the wait
        await ctx.storage.save_stall(stall)
    return thread


async def _restore_control(ctx: StallContext, thread: discord.Thread, stall: Stall) -> None:
    """Turn the closed note back into the Bump / Close stall buttons, in place."""
    from .views import ControlView

    if stall.control_message_id is not None:
        try:
            await thread.get_partial_message(stall.control_message_id).edit(
                content=control_text(ctx.settings, stall), view=ControlView(ctx), allowed_mentions=NO_MENTIONS
            )
            return
        except discord.NotFound:
            pass
    await _post_control(ctx, thread, stall)


# Helpers


async def _thread(ctx: StallContext, stall: Stall) -> discord.Thread:
    guild = ctx.bot.get_guild(stall.guild_id)
    if guild is None:
        raise StallError("The bot can't see this server right now.")
    return await fetch_thread(guild, stall.thread_id)


def find_tag(forum: discord.ForumChannel, name: str) -> discord.ForumTag | None:
    return next((t for t in forum.available_tags if t.name.lower() == name.lower()), None)


async def _set_status_tag(ctx: StallContext, thread: discord.Thread, status: str) -> None:
    """Swap the stall's status tag (Open / Closed), keeping any tags the member picked themselves."""
    forum = thread.parent
    if not isinstance(forum, discord.ForumChannel):
        return
    status_names = {name.lower() for name in ctx.settings.tags.values()}
    keep = [t for t in thread.applied_tags if t.name.lower() not in status_names]
    tag = find_tag(forum, ctx.settings.tag_name(status))
    wanted = ([tag] if tag else []) + keep[: MAX_POST_TAGS - 1]
    if [t.id for t in thread.applied_tags] != [t.id for t in wanted]:
        try:
            await thread.edit(applied_tags=wanted)
        except discord.HTTPException:
            log.warning("Couldn't set the %s tag on %s", status, thread.name)


async def setup_channel_problems(forum: discord.ForumChannel) -> list[str]:
    """Settings in the stalls channel that would let members get around the rules."""
    everyone = forum.permissions_for(forum.guild.default_role)
    problems = []
    if everyone.send_messages_in_threads:
        problems.append(
            "@everyone can **Send Messages in Posts**, so replies would bump stalls. Turn it off in this channel."
        )
    if not everyone.send_messages:
        problems.append("@everyone can't **Create Posts**, so members can't open stalls. Turn it on in this channel.")
    if forum.flags.require_tag and not any(not tag.moderated for tag in forum.available_tags):
        problems.append(
            "**Require people to select tags** is on, but every tag is staff-only, so members can't post. "
            "Turn that setting off, or add tags members can pick (e.g. Knives, Stickers)."
        )
    return problems


def channel_row(forum: discord.ForumChannel, panel_thread_id: int | None) -> StallChannel:
    return StallChannel(forum.id, forum.guild.id, panel_thread_id)
