"""Staff tools for managing posts, hidden from members: a right-click "Manage post" menu and /manage."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

from marketmule.core.staff import is_staff

from .forms import build_select
from .posting import fetch_thread
from .storage import Post
from .tags import find_tag, find_tags, set_thread_tags
from .templates import MAX_POST_TAGS, Template

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)


class ManagePostModal(ui.Modal, title="Manage post"):
    def __init__(self, forums: ForumsContext, post: Post, template: Template, thread: discord.Thread) -> None:
        super().__init__(timeout=15 * 60)
        self.forums = forums
        self.post = post
        self.template = template
        self.thread = thread

        current = {tag.name for tag in thread.applied_tags}
        self.original_tags = {f.key: sorted(o for o in f.options if o in current) for f in template.tag_fields}

        self.add_item(
            ui.TextDisplay(
                f"**{thread.name}**\n-# Changing the status notifies the reporter. Closing statuses also lock the post."
            )
        )
        self.status: ui.Select = ui.Select(
            options=[discord.SelectOption(label=s, value=s, default=s == post.status) for s in template.statuses]
        )
        self.add_item(ui.Label(text="Status", component=self.status))

        # A modal holds 5 items: the header, the status and up to 3 tag dropdowns.
        self.tag_selects: list[tuple[str, ui.Select]] = []
        for field in template.tag_fields[:3]:
            select = build_select(field, current)
            self.tag_selects.append((field.key, select))
            self.add_item(ui.Label(text=field.label, component=select))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        changes: list[str] = []

        new_status = self.status.values[0] if self.status.values else self.post.status
        if new_status != self.post.status:
            changes.append(await self._set_status(interaction.user, new_status))

        selected = {key: list(select.values) for key, select in self.tag_selects}
        if any(sorted(picked) != self.original_tags.get(key, []) for key, picked in selected.items()):
            changes.append(await self._set_tags(selected))

        await interaction.followup.send("\n".join(changes) or "No changes made.", ephemeral=True)

    async def _set_status(self, staff: discord.Member, status: str) -> str:
        thread = await fetch_thread(staff.guild, self.post.thread_id)
        if thread.archived or thread.locked:
            thread = await thread.edit(archived=False, locked=False)

        kept = [tag for tag in thread.applied_tags if tag.name not in self.template.statuses]
        status_tag = find_tag(thread.parent, status) if isinstance(thread.parent, discord.ForumChannel) else None
        tags = [*kept[: MAX_POST_TAGS - 1], status_tag] if status_tag else kept
        thread = await thread.edit(applied_tags=tags)

        await thread.send(
            f"{staff.mention} set the status to **{status}**. <@{self.post.reporter_id}>",
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(self.post.reporter_id)]
            ),
        )

        closing = status in self.template.final_statuses
        if closing:
            await thread.edit(locked=True, archived=True)
        await self.forums.storage.set_post_status(thread.id, status)
        log.info("%s set %s to %s", staff, thread.name, status)
        return f"Status set to **{status}**" + (" and the post is now locked." if closing else ".")

    async def _set_tags(self, selected: dict[str, list[str]]) -> str:
        thread = await fetch_thread(self.thread.guild, self.post.thread_id)
        forum = thread.parent
        if not isinstance(forum, discord.ForumChannel):
            return "Tags could not be updated."

        dropdown_tags = {option for f in self.template.tag_fields for option in f.options}
        kept = [tag for tag in thread.applied_tags if tag.name not in dropdown_tags]
        chosen = find_tags(forum, [name for names in selected.values() for name in names])
        thread = await set_thread_tags(thread, (kept + chosen)[:MAX_POST_TAGS])
        return "Tags updated: " + ", ".join(tag.name for tag in thread.applied_tags)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        log.exception("Manage post failed", exc_info=error)
        message = "Something went wrong saving that. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class ForumStaff(commands.Cog):
    def __init__(self, forums: ForumsContext) -> None:
        self.forums = forums
        # Context menus can't be declared inside a cog, so this one is added to the tree on load.
        self.manage_menu = app_commands.ContextMenu(name="Manage post", callback=self.manage_from_menu)

    async def cog_load(self) -> None:
        self.forums.bot.tree.add_command(self.manage_menu)

    async def cog_unload(self) -> None:
        self.forums.bot.tree.remove_command(self.manage_menu.name, type=self.manage_menu.type)

    @app_commands.default_permissions(manage_threads=True)
    @app_commands.guild_only()
    async def manage_from_menu(self, interaction: discord.Interaction, message: discord.Message) -> None:
        await self.open_manage_form(interaction)

    @app_commands.command(name="manage", description="Change this post's status or tags")
    @app_commands.default_permissions(manage_threads=True)
    @app_commands.guild_only()
    async def manage(self, interaction: discord.Interaction) -> None:
        await self.open_manage_form(interaction)

    async def open_manage_form(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await is_staff(self.forums.bot.db, interaction.user):
            await interaction.response.send_message("Only staff can manage posts.", ephemeral=True)
            return

        post = await self.forums.storage.get_post(interaction.channel_id or 0)
        template = self.forums.templates.get(post.template_id) if post else None
        if post is None or template is None:
            await interaction.response.send_message("Use this inside a post created by MarketMule.", ephemeral=True)
            return

        thread = await fetch_thread(interaction.guild, post.thread_id)
        await interaction.response.send_modal(ManagePostModal(self.forums, post, template, thread))
