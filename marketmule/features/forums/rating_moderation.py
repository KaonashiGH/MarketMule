"""Staff tools for ratings: remove a rating, and block members from rating.

    Apps → Remove rating     on a rating message: deletes it and takes its score out of the average
    /ratings block member    stops a member from rating (with an optional reason)
    /ratings unblock member  lets them rate again
    /ratings blocked         lists blocked members

All of these are hidden from members by default and check for staff when used.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from marketmule.core.staff import is_staff

from .posting import fetch_thread
from .ratings import NO_MENTIONS, format_score, refresh_scores
from .storage import MemberRating

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)


async def remove_rating(forums: ForumsContext, guild: discord.Guild, rating: MemberRating) -> str:
    """Delete a rating's message and its score, then refresh the post's averages. Returns a summary for staff."""
    post = await forums.storage.get_post(rating.thread_id)
    template = forums.templates.get(post.template_id) if post else None
    thread = await fetch_thread(guild, rating.thread_id)
    binding = await forums.storage.get_binding(thread.parent_id) if thread.parent_id else None

    if rating.message_id:
        try:
            if binding and binding.webhook_url:
                webhook = discord.Webhook.from_url(binding.webhook_url, client=forums.bot)
                await webhook.delete_message(rating.message_id, thread=thread)
            else:
                await thread.get_partial_message(rating.message_id).delete()
        except discord.NotFound:
            pass  # already deleted by hand
        except discord.HTTPException:
            # Webhook messages can only be deleted by the webhook; bot messages only by the bot. Try the other.
            try:
                await thread.get_partial_message(rating.message_id).delete()
            except discord.HTTPException:
                log.warning("Could not delete rating message %s", rating.message_id)

    await forums.storage.delete_rating(rating.thread_id, rating.rater_id)
    summary = await forums.storage.rating_summary(rating.thread_id)
    if template is not None:
        await refresh_scores(forums, template, thread, binding, summary)

    max_score = template.rating.max_score if template and template.rating else 0
    result = f"Removed <@{rating.rater_id}>'s rating ({rating.total}/{max_score})."
    if summary.count:
        result += f" The average is now {format_score(summary.average)}/{max_score} from {summary.count}."
    else:
        result += " The post has no ratings now."
    return result


@app_commands.default_permissions(manage_threads=True)
@app_commands.guild_only()
class RatingModeration(commands.GroupCog, group_name="ratings", group_description="Moderate member ratings"):
    def __init__(self, forums: ForumsContext) -> None:
        self.forums = forums
        super().__init__()
        # Context menus can't be declared inside a cog, so this one is added to the tree on load.
        self.remove_menu = app_commands.ContextMenu(name="Remove rating", callback=self.remove_from_menu)

    async def cog_load(self) -> None:
        self.forums.bot.tree.add_command(self.remove_menu)

    async def cog_unload(self) -> None:
        self.forums.bot.tree.remove_command(self.remove_menu.name, type=self.remove_menu.type)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_staff(self.forums.bot.db, interaction.user):
            return True
        await interaction.response.send_message("Only staff can moderate ratings.", ephemeral=True)
        return False

    @app_commands.default_permissions(manage_threads=True)
    @app_commands.guild_only()
    async def remove_from_menu(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if interaction.guild is None or not await is_staff(self.forums.bot.db, interaction.user):
            await interaction.response.send_message("Only staff can remove ratings.", ephemeral=True)
            return
        rating = await self.forums.storage.find_rating_by_message(message.id)
        if rating is None:
            await interaction.response.send_message(
                "That message isn't a rating. Right-click the rating itself, then Apps → Remove rating.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await remove_rating(self.forums, interaction.guild, rating)
        log.info("%s removed %s's rating in thread %s", interaction.user, rating.rater_id, rating.thread_id)
        await interaction.followup.send(
            result + "\n-# To stop them rating again, use `/ratings block`.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    @app_commands.command(name="block", description="Stop a member from rating posts")
    @app_commands.describe(member="Who to block", reason="Optional note for other staff")
    async def block(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        if interaction.guild is None:
            return
        await self.forums.storage.block_rater(interaction.guild.id, member.id, reason, interaction.user.id)
        log.info("%s blocked %s from rating (%s)", interaction.user, member, reason or "no reason")
        await interaction.response.send_message(
            f"{member.mention} can no longer rate posts. Their existing ratings stay; "
            "remove any with Apps → Remove rating.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    @app_commands.command(name="unblock", description="Let a blocked member rate posts again")
    @app_commands.describe(member="Who to unblock")
    async def unblock(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None:
            return
        removed = await self.forums.storage.unblock_rater(interaction.guild.id, member.id)
        message = f"{member.mention} can rate posts again." if removed else f"{member.mention} wasn't blocked."
        await interaction.response.send_message(message, ephemeral=True, allowed_mentions=NO_MENTIONS)

    @app_commands.command(name="blocked", description="List members blocked from rating")
    async def blocked(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        blocks = await self.forums.storage.list_rating_blocks(interaction.guild.id)
        if not blocks:
            await interaction.response.send_message("Nobody is blocked from rating.", ephemeral=True)
            return
        lines = [
            f"<@{b.user_id}> · blocked by <@{b.blocked_by}>" + (f" · {b.reason}" if b.reason else "") for b in blocks
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=NO_MENTIONS)
