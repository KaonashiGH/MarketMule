"""Doing things to stalls on Discord: take on a new post, remove a rule-breaking one, bump, close, reopen."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

import discord

from marketmule.core.discord_utils import fetch_thread
from marketmule.core.staff import is_staff

from .render import Picture, bad_links, closed_text, hours_text, picture_name, removal_message, wait_left
from .storage import CLOSED, DELETED, OPEN, Stall, StallChannel, now

if TYPE_CHECKING:
    from discord import ui

    from .context import StallContext

    Layout = Callable[[list[Picture]], ui.LayoutView]  # builds the control message for a set of pictures
    NewPicture = tuple[discord.Attachment, str, str]  # an upload, its file name under the stall, its description

log = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()
PICTURE_TYPES = {"png", "jpg", "jpeg", "gif", "webp"}
WEEK_IN_MINUTES = 10080  # the longest Discord waits before hiding a quiet post; the bot unhides open stalls anyway
MAX_POST_TAGS = 5
MISSING_BUTTONS = "This stall's buttons are missing. Press Bump to bring them back, then try again."


EXEMPT_SETTING = "stalls_staff_exempt"  # per server, in the shared settings table: "1" (default) or "0"


async def staff_exemption_on(ctx: StallContext, guild_id: int) -> bool:
    return await ctx.bot.db.get_setting(guild_id, EXEMPT_SETTING) != "0"


async def set_staff_exemption(ctx: StallContext, guild_id: int, on: bool) -> None:
    await ctx.bot.db.set_setting(guild_id, EXEMPT_SETTING, "1" if on else "0")


async def is_exempt(ctx: StallContext, guild: discord.Guild, user_id: int) -> bool:
    """Staff skip the stall limits (one stall each, the repost wait, bump waits) while the exemption is on.

    Links are still checked for everyone."""
    if not await staff_exemption_on(ctx, guild.id):
        return False
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
    except discord.HTTPException:
        return False
    return await is_staff(ctx.bot.db, member)


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


async def problem_with_new_post(
    ctx: StallContext, thread: discord.Thread, owner_id: int, content: str, *, exempt: bool = False
) -> str | None:
    """Why a new post can't be a stall, or None if it's fine.

    One stall per member; CSFloat and Steam links only; and a new post after deleting the old one counts
    as a bump, so it has to wait as long as the Bump button would (deleting and reposting can't skip it)."""
    existing = None if exempt else await ctx.storage.stall_of(thread.guild.id, owner_id)
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


async def post_edited(ctx: StallContext, stall: Stall, *, exempt: bool = False) -> bool:
    """The owner edited their post: it counts as activity, and bumps at most every edit_bump_hours.

    Returns True if the stall was bumped."""
    hours = 0 if exempt else ctx.settings.limits.edit_bump_hours
    return await bump(ctx, stall, hours=hours) == 0


async def _post_control(ctx: StallContext, thread: discord.Thread, stall: Stall) -> None:
    """Post the control message (a new message is what moves a forum post up), then delete the old one.

    The stall's pictures live on this message, so they're copied across to the new one."""
    from .views import ControlView  # the views use this module, so import here

    message = await _control_message(thread, stall)
    files = await _copy_files(thread, message.attachments) if message else []
    await _send_control(thread, stall, lambda pictures: ControlView(ctx, stall, pictures), files)


async def _control_message(thread: discord.Thread, stall: Stall) -> discord.Message | None:
    """The bot's message under the stall, or None if it's gone."""
    if stall.control_message_id is None:
        return None
    try:
        return await thread.fetch_message(stall.control_message_id)
    except discord.NotFound:
        return None
    except discord.HTTPException:
        log.warning("Couldn't load the control message in %s", thread.name)
        return None


def _pictures(attachments: Sequence[discord.Attachment | discord.File]) -> list[Picture]:
    return [Picture(a.filename, a.description or "") for a in attachments]


async def _copy_files(thread: discord.Thread, attachments: Sequence[discord.Attachment]) -> list[discord.File]:
    """The pictures on a message, ready to post again (same file names and descriptions)."""
    files = []
    for attachment in attachments:
        try:
            files.append(await attachment.to_file())
        except discord.HTTPException:
            log.warning("Couldn't copy a picture in %s", thread.name)
    return files


async def _new_file(new: NewPicture) -> discord.File:
    attachment, filename, description = new
    return await attachment.to_file(filename=filename, description=description or None)


async def _send_control(thread: discord.Thread, stall: Stall, layout: Layout, files: list[discord.File]) -> None:
    """Post a new control message with these pictures, then delete the old one."""
    old = stall.control_message_id
    sent = await thread.send(view=layout(_pictures(files)), files=files, allowed_mentions=NO_MENTIONS)
    stall.control_message_id = sent.id
    if old and old != sent.id:
        try:
            await thread.get_partial_message(old).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.warning("Couldn't delete the old control message in %s", thread.name)


async def _edit_control(
    thread: discord.Thread,
    stall: Stall,
    layout: Layout,
    message: discord.Message,
    keep: Sequence[discord.Attachment],
    new: Sequence[NewPicture] = (),
) -> None:
    """Change the control message where it is (it doesn't move up), keeping some pictures and adding new ones.

    Control messages from before picture rows can't always switch to the new layout by editing, so if
    Discord refuses, the message is posted again instead (once per stall)."""
    old_style = not message.flags.components_v2
    files = [await _new_file(picture) for picture in new]
    extra = {"content": None} if old_style else {}  # the new layout can't have plain message text
    try:
        await message.edit(
            view=layout(_pictures([*keep, *files])), attachments=[*keep, *files], allowed_mentions=NO_MENTIONS, **extra
        )
        return
    except discord.NotFound:
        raise
    except discord.HTTPException:
        if not old_style:
            raise
        log.info("Posting a new control message in %s (old layout couldn't be edited)", thread.name)
    files = await _copy_files(thread, keep) + [
        await _new_file(picture) for picture in new
    ]  # the first try used them up
    await _send_control(thread, stall, layout, files)


async def stall_pictures(ctx: StallContext, stall: Stall) -> list[Picture]:
    """The pictures under a stall, in order."""
    message = await _control_message(await _thread(ctx, stall), stall)
    return _pictures(message.attachments) if message else []


async def add_picture(
    ctx: StallContext, stall: Stall, attachment: discord.Attachment, description: str, *, exempt: bool = False
) -> int:
    """Add one picture, with the owner's description, as a new row under the stall. Returns how many it shows.

    Discord doesn't let anyone add pictures to a post by editing it, so they go on the bot's message.
    It counts as an edit: activity, and a bump if the edit wait allows."""
    from .views import ControlView

    if attachment.filename.lower().rsplit(".", 1)[-1] not in PICTURE_TYPES:
        raise StallError("Only pictures can be added (PNG, JPG, GIF or WEBP).")
    description = " ".join(description.split())
    links = bad_links(description, ctx.settings.allowed_link_hosts)
    if links:
        raise StallError(f"The description has a link that isn't allowed: <{links[0]}>")
    limit = ctx.settings.limits.max_pictures
    async with ctx.lock(stall.thread_id):
        thread = await _unarchived(ctx, stall)
        message = await _control_message(thread, stall)
        if message is None:
            raise StallError(MISSING_BUTTONS)
        if len(message.attachments) >= limit:
            raise StallError(f"Your stall already shows {limit} pictures. Remove one first.")
        keep = list(message.attachments)
        count = len(keep) + 1
        name = picture_name([a.filename for a in message.attachments], attachment.filename)
        layout: Layout = lambda pictures: ControlView(ctx, stall, pictures)  # noqa: E731
        await _edit_control(thread, stall, layout, message, keep, [(attachment, name, description)])
        await ctx.storage.save_stall(stall)
    await post_edited(ctx, stall, exempt=exempt)
    return count


async def remove_pictures(ctx: StallContext, stall: Stall, filenames: Sequence[str], *, exempt: bool = False) -> int:
    """Take the chosen pictures off the stall; the rest stay as they are. Returns how many are left."""
    from .views import ControlView

    async with ctx.lock(stall.thread_id):
        thread = await _unarchived(ctx, stall)
        message = await _control_message(thread, stall)
        if message is None:
            raise StallError(MISSING_BUTTONS)
        keep = [a for a in message.attachments if a.filename not in set(filenames)]
        if len(keep) != len(message.attachments):
            layout: Layout = lambda pictures: ControlView(ctx, stall, pictures)  # noqa: E731
            await _edit_control(thread, stall, layout, message, keep)
            await ctx.storage.save_stall(stall)
    await post_edited(ctx, stall, exempt=exempt)
    return len(keep)


async def hide_link_previews(thread: discord.Thread) -> None:
    """Hide the link previews Discord adds under a stall post. The links stay clickable.

    Changing another member's message like this needs Manage Messages in the channel."""
    message = await starter_message(thread)  # a full message: a partial one can't hide previews
    if message is None or message.flags.suppress_embeds:
        return
    try:
        await message.edit(suppress=True)
    except discord.HTTPException:
        log.warning("Couldn't hide link previews in %s (does the bot have Manage Messages?)", thread.name)


# Closing and reopening


async def close_stall(ctx: StallContext, stall: Stall, reason: str, *, by_staff: bool = False) -> None:
    """Swap the buttons for a note and a Reopen button, tag it Closed, then lock and hide the post."""
    from .views import ReopenView

    async with ctx.lock(stall.thread_id):
        thread = await _thread(ctx, stall)
        if thread.archived:
            thread = await thread.edit(archived=False)
        text = closed_text(ctx.settings, reason)
        layout: Layout = lambda pictures: ReopenView(ctx, text, pictures)  # noqa: E731
        await _show(thread, stall, layout)
        await _set_status_tag(ctx, thread, CLOSED)
        stall.status = CLOSED
        stall.closed_by_staff = by_staff
        await ctx.storage.save_stall(stall)  # saved before archiving, so the "unhide open stalls" listener skips it
        try:
            await thread.edit(locked=True, archived=True)
        except discord.HTTPException:
            log.warning("Couldn't lock and archive %s", thread.name)
    log.info("Closed stall %s: %s", stall.thread_id, reason)


async def reopen_stall(ctx: StallContext, stall: Stall, *, exempt: bool = False) -> discord.Thread:
    async with ctx.lock(stall.thread_id):
        thread = await _thread(ctx, stall)
        thread = await thread.edit(archived=False, locked=False)
        moment = now()
        stall.status = OPEN
        stall.closed_by_staff = False
        stall.active_at = moment
        await _set_status_tag(ctx, thread, OPEN)
        if exempt or wait_left(stall.bumped_at, moment, ctx.settings.limits.manual_bump_hours) == 0:
            stall.bumped_at = moment
            await _post_control(ctx, thread, stall)  # a new message: back at the top
        else:
            await _restore_control(ctx, thread, stall)  # same message, same spot: closing can't skip the wait
        await ctx.storage.save_stall(stall)
    return thread


async def _restore_control(ctx: StallContext, thread: discord.Thread, stall: Stall) -> None:
    """Turn the closed note back into the stall's buttons, in place."""
    from .views import ControlView

    await _show(thread, stall, lambda pictures: ControlView(ctx, stall, pictures))


async def _show(thread: discord.Thread, stall: Stall, layout: Layout) -> None:
    """Swap what the control message shows, keeping its pictures. Posts a new one if it was deleted."""
    message = await _control_message(thread, stall)
    if message is not None:
        try:
            await _edit_control(thread, stall, layout, message, list(message.attachments))
            return
        except discord.NotFound:
            pass  # deleted by hand: post a new one below
    await _send_control(thread, stall, layout, [])


# Helpers


async def _unarchived(ctx: StallContext, stall: Stall) -> discord.Thread:
    thread = await _thread(ctx, stall)
    return await thread.edit(archived=False) if thread.archived else thread


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
