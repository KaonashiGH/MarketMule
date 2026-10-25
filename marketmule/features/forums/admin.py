"""Forum setup commands: /forums setup, /forums remove, /forums list, /forums testpost.

A forum channel can offer several forms. Running /forums setup again with another form adds it
to the same channel and rebuilds the pinned Start here post with a section for each."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from marketmule.core.staff import get_staff_role

from .forms import FormPanel, panel_thread_name
from .posting import Submission, banner_file, fetch_thread, publish
from .ratings import offer_rating
from .samples import sample_answers
from .status import offer_author_close
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


def form_choices(forums: ForumsContext, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower()
    return [
        app_commands.Choice(name=t.name, value=t.id) for t in forums.templates.values() if current in t.name.lower()
    ][:25]


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class ForumAdmin(commands.GroupCog, group_name="forums", group_description="Set up form-based forum channels"):
    def __init__(self, forums: ForumsContext) -> None:
        self.forums = forums
        super().__init__()

    @app_commands.command(description="Add a form to a forum channel and refresh its pinned Start here post")
    @app_commands.describe(channel="Forum channel", template="Form to add. Run again to add a second form.")
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

        # Keep the channel's existing forms and add this one at the end.
        previous = await self.forums.storage.get_binding(forum.id)
        template_ids = previous.template_ids if previous else ()
        if form.id not in template_ids:
            template_ids = (*template_ids, form.id)
        forms = self._known(template_ids)

        try:
            created = await sync_forum_tags(forum, forms)
        except TagLimitError as exc:
            await interaction.followup.send(f"{forum.mention}: {exc} Remove some existing tags first.", ephemeral=True)
            return

        refreshed = await guild.fetch_channel(forum.id)
        if not isinstance(refreshed, discord.ForumChannel):
            await interaction.followup.send(f"{forum.mention} is no longer a forum channel.", ephemeral=True)
            return
        forum = refreshed

        webhook_url = previous.webhook_url if previous else None
        if not webhook_url:
            webhook_url = (await forum.create_webhook(name=WEBHOOK_NAME, reason=AUDIT_REASON)).url

        panel = await self._replace_panel(forum, previous, forms)
        await self.forums.storage.save_binding(
            Binding(
                channel_id=forum.id,
                guild_id=guild.id,
                template_ids=tuple(f.id for f in forms),
                webhook_url=webhook_url,
                panel_thread_id=panel.id,
            )
        )
        log.info("Set up #%s with %s", forum.name, ", ".join(f.id for f in forms))

        names = ", ".join(f"**{f.name}**" for f in forms)
        message = (
            f"{forum.mention} now offers: {names}. Created {created} tag(s) and pinned {panel.mention}.\n"
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
        return form_choices(self.forums, current)

    @app_commands.command(description="Post a filled-in test of a form, as you, without typing it out")
    @app_commands.describe(
        template="Which form to test",
        picture="Optional screenshot, for forms that ask for pictures",
    )
    async def testpost(
        self, interaction: discord.Interaction, template: str, picture: discord.Attachment | None = None
    ) -> None:
        """Answers come from each question's placeholder. Everything else is the real thing: same post,
        tags, buttons and ratings as a member's submission. Delete the post when you're done."""
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return
        form = self.forums.templates.get(template)
        if form is None:
            await interaction.response.send_message("Pick a form from the list.", ephemeral=True)
            return
        bindings = await self.forums.storage.list_bindings(guild.id)
        binding = next((b for b in bindings if form.id in b.template_ids), None)
        if binding is None:
            await interaction.response.send_message(
                f"No channel offers **{form.name}** yet. Run `/forums setup` first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        text, choices = sample_answers(form)
        uploads = [f for f in form.fields if f.kind == "file"]
        attachments = {uploads[0].key: [picture]} if picture and uploads else {}
        submission = Submission(form, binding, member, text=text, choices=choices, attachments=attachments)
        thread = await publish(self.forums, submission)
        await offer_author_close(self.forums, form, thread)
        await offer_rating(self.forums, form, thread)
        log.info("%s posted a test %s", member, form.id)
        await interaction.followup.send(f"Posted a test {form.name}: {thread.mention}", ephemeral=True)

    @testpost.autocomplete("template")
    async def testpost_template_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return form_choices(self.forums, current)

    @app_commands.command(description="Remove one form from a forum channel, or every form if none is picked")
    @app_commands.describe(channel="Forum channel", template="Form to remove. Leave empty to remove them all.")
    async def remove(self, interaction: discord.Interaction, channel: str, template: str | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            return
        forum = resolve_forum(guild, channel)
        binding = await self.forums.storage.get_binding(forum.id) if forum else None
        if forum is None or binding is None:
            await interaction.response.send_message("That channel isn't set up.", ephemeral=True)
            return
        if template is not None and template not in binding.template_ids:
            await interaction.response.send_message(f"{forum.mention} doesn't offer that form.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        remaining = self._known(t for t in binding.template_ids if template is not None and t != template)
        if not remaining:
            if binding.panel_thread_id:
                await self._delete_thread(guild, binding.panel_thread_id)
            await self.forums.storage.delete_binding(forum.id)
            await interaction.followup.send(
                f"Removed {forum.mention}. Existing posts are kept and can still be managed.", ephemeral=True
            )
            return

        panel = await self._replace_panel(forum, binding, remaining)
        await self.forums.storage.save_binding(
            Binding(
                channel_id=binding.channel_id,
                guild_id=binding.guild_id,
                template_ids=tuple(f.id for f in remaining),
                webhook_url=binding.webhook_url,
                panel_thread_id=panel.id,
            )
        )
        await interaction.followup.send(
            f"Removed that form from {forum.mention}. It still offers: {', '.join(f.name for f in remaining)}.",
            ephemeral=True,
        )

    @remove.autocomplete("channel")
    async def remove_channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return forum_choices(interaction, current)

    @remove.autocomplete("template")
    async def remove_template_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return form_choices(self.forums, current)

    @app_commands.command(name="list", description="Show which forums offer which forms")
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
            names = ", ".join(f"**{f.name}**" for f in self._known(binding.template_ids)) or "no forms"
            count = await self.forums.storage.count_posts(binding.channel_id)
            lines.append(f"<#{binding.channel_id}> → {names} ({count} posts)")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    def _known(self, template_ids: Iterable[str]) -> list[Template]:
        """Templates for these IDs, skipping any whose file has since been deleted."""
        return [self.forums.templates[t] for t in template_ids if t in self.forums.templates]

    async def _replace_panel(
        self, forum: discord.ForumChannel, previous: Binding | None, forms: list[Template]
    ) -> discord.Thread:
        """Post a fresh pinned Start here post for these forms and delete the old one."""
        # Delete the old one first: a forum can only have one pinned post.
        if previous and previous.panel_thread_id:
            await self._delete_thread(forum.guild, previous.panel_thread_id)
        return await self._post_panel(forum, forms)

    async def _post_panel(self, forum: discord.ForumChannel, forms: list[Template]) -> discord.Thread:
        """Create the locked, pinned post that holds each form's Start button."""
        tags = []
        if forum.flags.require_tag:
            type_tag = find_tag(forum, forms[0].type_tag)
            tags = [type_tag] if type_tag else []
        config = getattr(self.forums.bot, "config", None)
        banners = [f for t in forms if (f := banner_file(getattr(config, "banners_dir", None), t))]
        created = await forum.create_thread(
            name=panel_thread_name(forms),
            view=FormPanel(self.forums, forms, banners={f.filename for f in banners}),
            files=banners,
            applied_tags=tags,
        )
        for thread in forum.threads:  # unpin anything else, e.g. a post pinned by hand
            if thread.flags.pinned and thread.id != created.thread.id:
                await thread.edit(pinned=False)
        return await created.thread.edit(pinned=True, locked=True)

    @staticmethod
    async def _delete_thread(guild: discord.Guild, thread_id: int) -> None:
        try:
            await (await fetch_thread(guild, thread_id)).delete()
        except discord.HTTPException:
            log.info("Old panel thread %s was already gone", thread_id)
