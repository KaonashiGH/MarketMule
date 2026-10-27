"""Changing a post's status, by staff (Manage post) or by the person who posted it (the close button).

A template can let the poster close their own post. For example, price checks get a
"Mark as priced" button. Set it up in the template:

    "author_close": {"status": "Priced", "button_label": "Mark as priced", "text": "..."}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import ui

from marketmule.core.staff import is_staff

from .posting import fetch_thread, retitle
from .storage import Post
from .tags import find_tag
from .templates import MAX_POST_TAGS, Template

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)

CLOSE_BUTTON_PREFIX = "marketmule:forums:close:"


async def set_status(
    forums: ForumsContext,
    post: Post,
    template: Template,
    actor: discord.Member,
    status: str,
    *,
    announcement: str | None = None,
    view: ui.View | None = None,
) -> str:
    """Swap the post's status tag, announce it in the post, and lock the post if the status is final.

    `announcement` replaces the usual "set the status to" message, e.g. a staff shortcut's ready-made reply.
    It's posted before the post is locked, so it's the last message in it.
    """
    thread = await fetch_thread(actor.guild, post.thread_id)
    if thread.archived or thread.locked:
        thread = await thread.edit(archived=False, locked=False)

    kept = [tag for tag in thread.applied_tags if tag.name not in template.statuses]
    status_tag = find_tag(thread.parent, status) if isinstance(thread.parent, discord.ForumChannel) else None
    tags = [status_tag, *kept[: MAX_POST_TAGS - 1]] if status_tag else kept  # status always comes first
    name = retitle(thread.name, template, post.status, status)  # e.g. 🔵 Needs pricing → 🟢 Priced
    thread = await thread.edit(applied_tags=tags, **({"name": name} if name else {}))

    if announcement is not None:
        await thread.send(
            announcement,
            **({"view": view} if view is not None else {}),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(post.reporter_id)]
            ),
        )
    elif actor.id == post.reporter_id:
        await thread.send(f"{actor.mention} marked this post as **{status}**.")
    else:
        # Staff changed it, so ping the person who posted.
        await thread.send(
            f"{actor.mention} set the status to **{status}**. <@{post.reporter_id}>",
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(post.reporter_id)]
            ),
        )

    closing = status in template.final_statuses
    if closing:
        await thread.edit(locked=True, archived=True)
    await forums.storage.set_post_status(thread.id, status)
    log.info("%s set %s to %s", actor, thread.name, status)
    return f"Status set to **{status}**" + (" and the post is now locked." if closing else ".")


class AuthorCloseView(ui.View):
    """The button the bot posts inside a new post so its author can close it, e.g. "Mark as priced"."""

    def __init__(self, forums: ForumsContext, template: Template, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.forums = forums
        self.template = template
        if template.author_close is None:
            return
        button: ui.Button = ui.Button(
            label=template.author_close.button_label,
            style=discord.ButtonStyle.success,
            custom_id=CLOSE_BUTTON_PREFIX + template.id,
            disabled=disabled,
        )
        button.callback = self.close
        self.add_item(button)

    async def close(self, interaction: discord.Interaction) -> None:
        settings = self.template.author_close
        post = await self.forums.storage.get_post(interaction.channel_id or 0)
        if settings is None or post is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This button isn't available here.", ephemeral=True)
            return
        if interaction.user.id != post.reporter_id and not await is_staff(self.forums.bot.db, interaction.user):
            await interaction.response.send_message("Only the person who posted this can close it.", ephemeral=True)
            return
        if post.status in self.template.final_statuses:
            await interaction.response.send_message("This post is already closed.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        # Grey out the button first, while the post can still be edited.
        if interaction.message is not None:
            await interaction.message.edit(view=AuthorCloseView(self.forums, self.template, disabled=True))
        result = await set_status(self.forums, post, self.template, interaction.user, settings.status)
        await interaction.followup.send(result, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item, /) -> None:
        log.exception("Close button failed", exc_info=error)
        message = "Something went wrong closing this post. Please let a staff member know."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def offer_author_close(forums: ForumsContext, template: Template, thread: discord.Thread) -> None:
    """Post the close button in a new post, if the template has one. A failure here never blocks the post."""
    if template.author_close is None:
        return
    try:
        await thread.send(template.author_close.text, view=AuthorCloseView(forums, template))
    except discord.HTTPException:
        log.warning("Could not add the close button in %s", thread.name)
