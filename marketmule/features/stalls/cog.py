"""Stall commands, the events that watch members' posts, and the upkeep loop.

Events:
    - a member makes a post in the stalls channel: it becomes their stall (Open tag + Bump / Close buttons),
      unless they already have one or it links outside CSFloat and Steam, in which case it's removed and
      they're DMed why, with a copy of what they wrote.
    - the owner edits their post: the links are checked again, and the edit bumps the stall at most
      every edit_bump_hours.
    - Discord hides a quiet post after a week: open stalls are unhidden straight away.
    - a stall post is deleted: its owner can open a new one.

Every UPKEEP_HOURS the bot closes stalls with no edits or bumps for inactive_days, and unhides open ones.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from marketmule.core.discord_utils import fetch_thread

from . import service
from .render import bad_links, is_inactive
from .storage import OPEN, StallChannel, now
from .views import StallPanel

if TYPE_CHECKING:
    from .context import StallContext

log = logging.getLogger(__name__)

AUDIT_REASON = "MarketMule stall setup"
UPKEEP_HOURS = 2
MAX_FORUM_TAGS = 20

REQUIRED_PERMISSIONS = {
    "view_channel": "View Channel",
    "manage_channels": "Manage Channels",
    "manage_threads": "Manage Threads",
    "send_messages": "Create Posts",
    "send_messages_in_threads": "Send Messages in Posts",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "read_message_history": "Read Message History",
}
NO_CONTENT_WARNING = (
    "The bot can't read message text (Message Content Intent is off), so links in stalls aren't checked. "
    "Turn it on: Discord Developer Portal → your app → Bot → Message Content Intent, then restart the bot."
)


def missing_permissions(forum: discord.ForumChannel) -> list[str]:
    granted = forum.permissions_for(forum.guild.me)
    return [label for attr, label in REQUIRED_PERMISSIONS.items() if not getattr(granted, attr)]


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class Stalls(commands.GroupCog, group_name="stalls", group_description="Set up and manage member stalls"):
    def __init__(self, ctx: StallContext) -> None:
        self.ctx = ctx
        self._seen_edits: dict[int, str] = {}  # thread → last edit time seen, to ignore repeated edit events
        super().__init__()

    async def cog_load(self) -> None:
        if not self._can_read_text():
            log.warning(NO_CONTENT_WARNING)
        self.upkeep.start()

    async def cog_unload(self) -> None:
        self.upkeep.cancel()

    def _can_read_text(self) -> bool:
        intents = getattr(self.ctx.bot, "intents", None)
        return bool(intents and intents.message_content)

    # Commands

    # Setup lives in /forums setup (pick "Stalls" as the template), so every forum channel is set up the same way.
    # /forums finds this cog by name ("Stalls") and calls these methods: the two features don't import each other.

    async def is_stalls_channel(self, channel_id: int) -> bool:
        return await self.ctx.storage.get_channel(channel_id) is not None

    async def list_lines(self, guild_id: int) -> list[str]:
        lines = []
        for channel in await self.ctx.storage.list_channels(guild_id):
            open_count, closed_count = await self.ctx.storage.count_stalls(channel.channel_id)
            lines.append(f"<#{channel.channel_id}> → **Stalls** ({open_count} open, {closed_count} closed)")
        return lines

    async def setup_channel(self, interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
        """Make a forum the stalls channel: tags, sort order, and the pinned Start here post."""
        guild = interaction.guild
        if guild is None:
            return
        missing = missing_permissions(channel)
        if missing:
            await interaction.response.send_message(
                f"The bot is missing these permissions in {channel.mention}: {', '.join(missing)}.", ephemeral=True
            )
            return
        if await self._is_forms_channel(channel.id):
            await interaction.response.send_message(
                f"{channel.mention} is set up for forms. Use a separate forum channel for stalls.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        settings = self.ctx.settings
        names = list(settings.tags.values())
        existing = {tag.name.lower() for tag in channel.available_tags}
        to_create = [name for name in names if name.lower() not in existing]
        if len(channel.available_tags) + len(to_create) > MAX_FORUM_TAGS:
            await interaction.followup.send(
                f"{channel.mention} has too many tags to add {', '.join(to_create)}. Remove some first.",
                ephemeral=True,
            )
            return
        for name in to_create:
            emoji = settings.tag_emojis.get(name)
            await channel.create_tag(
                name=name,
                moderated=True,  # only staff and the bot can add status tags
                emoji=discord.PartialEmoji(name=emoji) if emoji else None,
                reason=AUDIT_REASON,
            )

        forum = await guild.fetch_channel(channel.id)
        if not isinstance(forum, discord.ForumChannel):
            await interaction.followup.send(f"{channel.mention} is no longer a forum channel.", ephemeral=True)
            return
        # Tag emojis, and newest activity first so a bump moves a stall to the top.
        tags = list(forum.available_tags)
        changed = forum.default_sort_order is not discord.ForumOrderType.latest_activity
        for tag in tags:
            name = next((n for n in names if n.lower() == tag.name.lower()), "")
            emoji = settings.tag_emojis.get(name)
            if emoji and str(tag.emoji or "") != emoji:
                tag.emoji = discord.PartialEmoji(name=emoji)
                changed = True
        if changed:
            forum = await forum.edit(
                available_tags=tags, default_sort_order=discord.ForumOrderType.latest_activity, reason=AUDIT_REASON
            )

        previous = await self.ctx.storage.get_channel(forum.id)
        if previous and previous.panel_thread_id:
            await self._delete_thread(guild, previous.panel_thread_id)
        panel = await self._post_panel(forum)
        await self.ctx.storage.save_channel(StallChannel(forum.id, guild.id, panel.id))
        log.info("Set up stalls in #%s", forum.name)

        lines = [f"{forum.mention} is the stalls channel. Created {len(to_create)} tag(s) and pinned {panel.mention}."]
        problems = await service.setup_channel_problems(forum)
        if not self._can_read_text():
            problems.append(NO_CONTENT_WARNING)
        if problems:
            lines.append("**Still to fix:**\n" + "\n".join(f"- {p}" for p in problems))
        else:
            lines.append("Channel permissions look right: members can create posts but not reply.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    async def remove_channel(self, interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
        """Stop using a forum for stalls. Existing posts stay."""
        guild = interaction.guild
        previous = await self.ctx.storage.get_channel(channel.id)
        if guild is None or previous is None:
            await interaction.response.send_message("That channel isn't a stalls channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if previous.panel_thread_id:
            await self._delete_thread(guild, previous.panel_thread_id)
        await self.ctx.storage.delete_channel(channel.id)
        await interaction.followup.send(
            f"{channel.mention} is no longer a stalls channel. Existing posts stay as they are.", ephemeral=True
        )

    @app_commands.command(description="Close a member's stall (only staff can reopen it)")
    @app_commands.describe(member="Whose stall to close", reason="Shown in the stall. Leave empty for a standard note.")
    async def close(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        stall = await self.ctx.storage.stall_of(member.guild.id, member.id)
        if stall is None or stall.is_closed:
            await interaction.response.send_message(f"{member.mention} has no open stall.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        note = f"Closed by staff: {reason}" if reason else "Closed by staff."
        try:
            await service.close_stall(self.ctx, stall, note[:1500], by_staff=True)
        except Exception:
            log.exception("Staff close failed")
            await interaction.followup.send("Couldn't close that stall.", ephemeral=True)
            return
        await interaction.followup.send(f"Closed {member.mention}'s stall.", ephemeral=True)

    @app_commands.command(description="Reopen a member's stall, including one staff closed")
    @app_commands.describe(member="Whose stall to reopen")
    async def reopen(self, interaction: discord.Interaction, member: discord.Member) -> None:
        stall = await self.ctx.storage.stall_of(member.guild.id, member.id)
        if stall is None or not stall.is_closed:
            await interaction.response.send_message(f"{member.mention} has no closed stall.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            thread = await service.reopen_stall(self.ctx, stall)
        except Exception:
            log.exception("Staff reopen failed")
            await interaction.followup.send("Couldn't reopen that stall.", ephemeral=True)
            return
        await interaction.followup.send(f"Reopened {member.mention}'s stall: {thread.jump_url}", ephemeral=True)

    @app_commands.command(name="list", description="Show the stalls channel and how many stalls it has")
    async def list_channels(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        channels = await self.ctx.storage.list_channels(interaction.guild.id)
        if not channels:
            await interaction.response.send_message("No stalls channel yet. Use `/stalls setup`.", ephemeral=True)
            return
        lines = []
        for channel in channels:
            open_count, closed_count = await self.ctx.storage.count_stalls(channel.channel_id)
            lines.append(f"<#{channel.channel_id}> → {open_count} open, {closed_count} closed")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # Members' posts

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        """A new post in the stalls channel becomes a stall, or is removed if it breaks the rules."""
        if thread.parent_id is None or await self.ctx.storage.get_channel(thread.parent_id) is None:
            return
        bot_user = self.ctx.bot.user
        owner_id = thread.owner_id
        if owner_id is None or (bot_user is not None and owner_id == bot_user.id):
            return  # the Start here post
        if await self.ctx.storage.get_stall(thread.id) is not None:
            return
        try:
            message = await service.starter_message(thread)
            content = message.content if message else ""
            problem = await service.problem_with_new_post(self.ctx, thread, owner_id, content)
            if problem:
                await service.remove_post(self.ctx, thread, owner_id, problem, content)
                return
            try:
                await service.register_stall(self.ctx, thread, owner_id)
            except sqlite3.IntegrityError:  # two posts at once: the first one counts
                existing = await self.ctx.storage.stall_of(thread.guild.id, owner_id)
                link = f"<#{existing.thread_id}>" if existing else "your other post"
                reason = self.ctx.settings.removed.duplicate.format(link=link)
                await service.remove_post(self.ctx, thread, owner_id, reason, content)
        except Exception:
            log.exception("Couldn't set up stall post %s", thread.id)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """The owner edited their post: check its links again, and bump it if the edit cooldown allows."""
        if payload.message_id != payload.channel_id:
            return  # only a post's first message has the same ID as the post
        edited_at = payload.data.get("edited_timestamp")
        if not edited_at or self._seen_edits.get(payload.message_id) == edited_at:
            return  # not a real edit (e.g. Discord adding a link preview), or one already handled
        stall = await self.ctx.storage.get_stall(payload.channel_id)
        if stall is None or stall.is_closed or stall.is_deleted:
            return
        self._seen_edits[payload.message_id] = edited_at
        try:
            content = payload.data.get("content") or ""
            links = bad_links(content, self.ctx.settings.allowed_link_hosts)
            if links:
                guild = self.ctx.bot.get_guild(stall.guild_id)
                if guild is None:
                    return
                thread = await fetch_thread(guild, stall.thread_id)
                reason = self.ctx.settings.removed.bad_link.format(links=", ".join(f"<{link}>" for link in links[:5]))
                await service.remove_post(self.ctx, thread, stall.owner_id, reason, content)
                await self.ctx.storage.mark_deleted(stall.thread_id)
                return
            await service.post_edited(self.ctx, stall)
        except Exception:
            log.exception("Couldn't handle the edit to stall %s", stall.thread_id)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        """Discord hides forum posts after a week without messages. Open stalls are unhidden right away."""
        if not after.archived or before.archived:
            return
        stall = await self.ctx.storage.get_stall(after.id)
        if stall is not None and not stall.is_closed:
            try:
                await after.edit(archived=False)
            except discord.HTTPException:
                log.warning("Couldn't unhide stall %s", after.name)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        """A deleted stall post frees its owner to post a new one, once the bump wait has passed."""
        if await self.ctx.storage.get_stall(payload.thread_id) is not None:
            await self.ctx.storage.mark_deleted(payload.thread_id)
            log.info("Stall %s was deleted", payload.thread_id)
        self._seen_edits.pop(payload.thread_id, None)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """The owner deleted their post's text. An empty stall isn't a stall, so the post goes too."""
        if payload.message_id != payload.channel_id:
            return  # only a post's first message has the same ID as the post
        stall = await self.ctx.storage.get_stall(payload.channel_id)
        guild = self.ctx.bot.get_guild(stall.guild_id) if stall else None
        if stall is None or stall.is_deleted or guild is None:
            return
        try:
            thread = await fetch_thread(guild, stall.thread_id)
            await thread.delete()
        except (discord.HTTPException, RuntimeError):
            log.warning("Couldn't remove emptied stall %s", stall.thread_id)
        await self.ctx.storage.mark_deleted(stall.thread_id)

    # Upkeep

    @tasks.loop(hours=UPKEEP_HOURS)
    async def upkeep(self) -> None:
        moment = now()
        days = self.ctx.settings.limits.inactive_days
        for stall in await self.ctx.storage.open_stalls():
            try:
                guild = self.ctx.bot.get_guild(stall.guild_id)
                if guild is None:
                    continue
                try:
                    thread = await fetch_thread(guild, stall.thread_id)
                except discord.NotFound:  # deleted while the bot was offline
                    await self.ctx.storage.mark_deleted(stall.thread_id)
                    continue
                if is_inactive(stall, moment, days):
                    reason = f"No edits or bumps for {days} days. The owner can reopen it any time."
                    await service.close_stall(self.ctx, stall, reason)
                elif thread.archived:
                    await thread.edit(archived=False)
            except Exception:
                log.exception("Upkeep failed for stall %s", stall.thread_id)

    @upkeep.before_loop
    async def before_upkeep(self) -> None:
        await self.ctx.bot.wait_until_ready()

    # Helpers

    async def _is_forms_channel(self, channel_id: int) -> bool:
        try:
            row = await self.ctx.bot.db.fetch_one("SELECT 1 FROM forum_bindings WHERE channel_id = ?", channel_id)
        except Exception:  # the forums feature isn't loaded, so its table may not exist
            return False
        return row is not None

    async def _post_panel(self, forum: discord.ForumChannel) -> discord.Thread:
        """The locked, pinned Start here post with the Get the template button."""
        settings = self.ctx.settings
        banners_dir = getattr(getattr(self.ctx.bot, "config", None), "banners_dir", None)
        files = []
        if settings.banner and banners_dir is not None and (banners_dir / f"{settings.banner}.png").is_file():
            files.append(discord.File(banners_dir / f"{settings.banner}.png", filename=settings.banner_filename))
        # With "Require people to select tags" on, Discord refuses a post without a tag, the bot's included.
        open_tag = service.find_tag(forum, settings.tag_name(OPEN)) if forum.flags.require_tag else None
        created = await forum.create_thread(
            name=settings.panel.thread_name,
            view=StallPanel(self.ctx, banner=settings.banner_filename if files else None),
            files=files,
            applied_tags=[open_tag] if open_tag else [],
        )
        for thread in forum.threads:  # a forum can only have one pinned post
            if thread.flags.pinned and thread.id != created.thread.id:
                await thread.edit(pinned=False)
        return await created.thread.edit(pinned=True, locked=True)

    @staticmethod
    async def _delete_thread(guild: discord.Guild, thread_id: int) -> None:
        try:
            await (await fetch_thread(guild, thread_id)).delete()
        except (discord.HTTPException, RuntimeError):
            log.info("Old stalls Start here post %s was already gone", thread_id)
