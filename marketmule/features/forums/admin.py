"""Forum setup commands: /forums setup, /forums remove, /forums list."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from marketmule.core.staff import get_staff_role

from .forms import StartView
from .posting import fetch_thread
from .storage import Binding
from .tags import AUDIT_REASON, TagLimitError, find_tag, sync_forum_tags
from .templates import Template

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)

WEBHOOK_NAME = "MarketMule posts"

REQUIRED_PERMISSIONS = {
    "view_channel": "View Channel",
    "manage_channels": "Manage Channels",
    "manage_webhooks": "Manage Webhooks",
    "manage_threads": "Manage Threads",
    "send_messages": "Create Posts",
    "send_messages_in_threads": "Send Messages in Posts",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "read_message_history": "Read Message History",
    "add_reactions": "Add Reactions",
}


def visible_forums(guild: discord.Guild) -> list[discord.ForumChannel]:
    return [channel for channel in guild.forums if channel.permissions_for(guild.me).view_channel]


def resolve_forum(guild: discord.Guild, value: str) -> discord.ForumChannel | None:
    """Accept a channel ID (from autocomplete), a mention, or a typed name like #bug-reports."""
    value = value.strip()
    match = re.fullmatch(r"<#(\d+)>|(\d+)", value)
    if match:
        channel = guild.get_channel(int(match.group(1) or match.group(2)))
    else:
        name = value.lstrip("#").lower()
        channel = discord.utils.find(lambda c: c.name.lower() == name, guild.channels)
    return channel if isinstance(channel, discord.ForumChannel) else None


def describe_missing_forum(guild: discord.Guild, value: str) -> str:
    name = value.strip().lstrip("<#").rstrip(">").lstrip("#").lower()
    match = discord.utils.find(lambda c: c.name.lower() == name or str(c.id) == name, guild.channels)
    lines = []
    if match is not None and not isinstance(match, discord.ForumChannel):
        lines.append(f"{match.mention} is not a forum channel.")
    elif match is None:
        lines.append(f"Couldn't find a channel called `{value}`.")

    visible = visible_forums(guild)
    hidden = [forum for forum in guild.forums if forum not in visible]
    lines.append("Forums the bot can see: " + (", ".join(f.mention for f in visible) or "none"))
    if hidden:
        lines.append("Forums the bot can't see (grant View Channel): " + ", ".join(f"#{f.name}" for f in hidden))
    return "\n".join(lines)


def missing_permissions(forum: discord.ForumChannel) -> list[str]:
    granted = forum.permissions_for(forum.guild.me)
    return [label for attr, label in REQUIRED_PERMISSIONS.items() if not getattr(granted, attr)]


def forum_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    current = current.lower()
    return [
        app_commands.Choice(name=f"#{forum.name}", value=str(forum.id))
        for forum in visible_forums(interaction.guild)
        if current in forum.name.lower()
    ][:25]


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class ForumAdmin(commands.GroupCog, group_name="forums", group_description="Set up form-based forum channels"):
    def __init__(self, forums: ForumsContext) -> None:
        self.forums = forums
        super().__init__()

    @app_commands.command(description="Connect a forum channel to a form and post its pinned Start here message")
    @app_commands.describe(channel="Forum channel", template="Form used in this channel")
    async def setup(self, interaction: discord.Interaction, channel: str, template: str) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        forum = resolve_forum(guild, channel)
        if forum is None:
            await interaction.followup.send(describe_missing_forum(guild, channel), ephemeral=True)
            return
        form = self.forums.templates.get(template)
        if form is None:
            await interaction.followup.send("Pick a form from the list.", ephemeral=True)
            return
        missing = missing_permissions(forum)
        if missing:
            await interaction.followup.send(
                f"The bot is missing these permissions in {forum.mention}: {', '.join(missing)}.\n"
                "Add the bot's role to the channel's permissions and allow them.",
                ephemeral=True,
            )
            return

        try:
            created = await sync_forum_tags(forum, form)
        except TagLimitError as exc:
            await interaction.followup.send(f"{forum.mention}: {exc} Remove some existing tags first.", ephemeral=True)
            return

        refreshed = await guild.fetch_channel(forum.id)
        if not isinstance(refreshed, discord.ForumChannel):
            await interaction.followup.send(f"{forum.mention} is no longer a forum channel.", ephemeral=True)
            return
        forum = refreshed

        previous = await self.forums.storage.get_binding(forum.id)
        webhook_url = previous.webhook_url if previous else None
        if not webhook_url:
            webhook_url = (await forum.create_webhook(name=WEBHOOK_NAME, reason=AUDIT_REASON)).url
        if previous and previous.panel_thread_id:
            await self._delete_thread(guild, previous.panel_thread_id)

        panel = await self._post_panel(forum, form)
        await self.forums.storage.save_binding(
            Binding(
                channel_id=forum.id,
                guild_id=guild.id,
                template_id=form.id,
                webhook_url=webhook_url,
                panel_thread_id=panel.id,
            )
        )
        log.info("Set up #%s with %s", forum.name, form.id)

        message = (
            f"{forum.mention} now uses the **{form.name}** form. "
            f"Created {created} tag(s) and pinned {panel.mention}.\n"
            "Make sure @everyone has **Create Posts** off and **Send Messages in Posts** on in this channel."
        )
        if await get_staff_role(self.forums.bot.db, guild.id) is None:
            message += "\nRun `/marketmule staffrole` to choose which role can manage posts."
        await interaction.followup.send(message, ephemeral=True)

    @setup.autocomplete("channel")
    async def setup_channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return forum_choices(interaction, current)

    @setup.autocomplete("template")
    async def setup_template_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.lower()
        return [
            app_commands.Choice(name=t.name, value=t.id)
            for t in self.forums.templates.values()
            if current in t.name.lower()
        ][:25]

    @app_commands.command(description="Stop using a form in a forum channel")
    @app_commands.describe(channel="Forum channel")
    async def remove(self, interaction: discord.Interaction, channel: str) -> None:
        guild = interaction.guild
        if guild is None:
            return
        forum = resolve_forum(guild, channel)
        binding = await self.forums.storage.get_binding(forum.id) if forum else None
        if forum is None or binding is None:
            await interaction.response.send_message("That channel isn't set up.", ephemeral=True)
            return

        if binding.panel_thread_id:
            await self._delete_thread(guild, binding.panel_thread_id)
        await self.forums.storage.delete_binding(forum.id)
        await interaction.response.send_message(
            f"Removed {forum.mention}. Existing posts are kept and can still be managed.", ephemeral=True
        )

    @remove.autocomplete("channel")
    async def remove_channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return forum_choices(interaction, current)

    @app_commands.command(name="list", description="Show which forums use which form")
    async def list_bindings(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        bindings = await self.forums.storage.list_bindings(guild.id)
        if not bindings:
            await interaction.response.send_message("No forums are set up yet. Use `/forums setup`.", ephemeral=True)
            return

        lines = []
        for binding in bindings:
            template = self.forums.templates.get(binding.template_id)
            name = template.name if template else binding.template_id
            count = await self.forums.storage.count_posts(binding.channel_id)
            lines.append(f"<#{binding.channel_id}> → **{name}** ({count} posts)")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _post_panel(self, forum: discord.ForumChannel, template: Template) -> discord.Thread:
        """Create the locked, pinned post that holds the form's Start button."""
        embed = discord.Embed(title=template.panel.title, description=template.panel.text, color=template.color)
        tags = []
        if forum.flags.require_tag:
            type_tag = find_tag(forum, template.type_tag)
            tags = [type_tag] if type_tag else []
        created = await forum.create_thread(
            name=template.panel.thread_name,
            embed=embed,
            view=StartView(self.forums, template.panel.button_label, template.panel.button_style),
            applied_tags=tags,
        )
        return await created.thread.edit(pinned=True, locked=True)

    @staticmethod
    async def _delete_thread(guild: discord.Guild, thread_id: int) -> None:
        try:
            await (await fetch_thread(guild, thread_id)).delete()
        except discord.HTTPException:
            log.info("Old panel thread %s was already gone", thread_id)
