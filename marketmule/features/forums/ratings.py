"""Member ratings: a rubric form under posts (e.g. loadouts and trade-ups) and the /leaderboard command.

How it works:
    - Forms with a "rating" block get a bot message under each new post with a Rate button.
    - The button opens the rubric: a 1-10 dropdown per category, plus an optional verdict and note.
    - The rating is posted in the thread under the reviewer's name, in the rubric's format.
    - Each member has one rating per post. Rating again edits their earlier rating.
    - Members can't rate their own posts, or posts that are closed.
    - The average shows on the button message and in the post's top line.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

from .posting import fetch_thread, webhook_username
from .storage import Binding, MemberRating, Post, RatingSummary
from .templates import MAX_CATEGORY_SCORE, MAX_PAGE_ITEMS, Rating, RatingCategory, Template

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)

RATE_BUTTON_PREFIX = "marketmule:forums:rate:"
GUIDE_BUTTON_PREFIX = "marketmule:forums:guide:"
FORM_TIMEOUT = 15 * 60
NO_MENTIONS = discord.AllowedMentions.none()
MEDALS = ("🥇", "🥈", "🥉")

# The " · ⭐ 31.5/40" added to the end of a post's top line.
SCORE_SUFFIX = re.compile(r" · \S+ [\d.]+/\d+$")


# Text formatting (no Discord calls, so these are easy to test)


def format_score(value: float) -> str:
    """31.5 stays 31.5, and 32.0 becomes 32."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def noun(template: Template) -> str:
    return template.name.lower()


def summary_text(template: Template, summary: RatingSummary) -> str:
    """The line on the bot's rating message, e.g. "⭐ 31.5 / 40 average from 4 ratings"."""
    rating = template.rating
    if rating is None:
        return ""
    if summary.count == 0:
        return f"-# {rating.emoji} No ratings yet. Be the first to rate this {noun(template)}!"
    plural = "rating" if summary.count == 1 else "ratings"
    return (
        f"-# {rating.emoji} **{format_score(summary.average)} / {rating.max_score}** average from "
        f"{summary.count} {plural}. Already rated? Rating again updates yours."
    )


def with_score(top_line: str, rating: Rating, summary: RatingSummary) -> str:
    """Add (or update) the average at the end of a post's top line."""
    base = SCORE_SUFFIX.sub("", top_line)
    if summary.count == 0:
        return base
    return f"{base} · {rating.emoji} {format_score(summary.average)}/{rating.max_score}"


def render_rating(rating: Rating, member: MemberRating) -> str:
    """The rating as posted in the thread, following the rubric's layout."""
    lines = [f"> **{rating.title}**"]
    lines += [f"> {c.label}: **{member.scores.get(c.key, 0)}**/{MAX_CATEGORY_SCORE}" for c in rating.categories]
    lines += [">", f"> **Final Score: {member.total} / {rating.max_score}**"]
    if member.verdict and rating.verdict_label:
        lines.append(f"> **{rating.verdict_label}:** {member.verdict}")
    if member.note and rating.note_label:
        first, *rest = member.note.strip().split("\n")
        lines.append(f"> **{rating.note_label}:** {first}")
        lines += [f"> {line}" for line in rest]
    return "\n".join(lines)


@dataclass(frozen=True)
class RatingItem:
    """One input in the rating form: a category score, the verdict, or the note."""

    kind: str  # "score", "verdict" or "note"
    category: RatingCategory | None = None


def rating_pages(rating: Rating) -> list[list[RatingItem]]:
    """Split the rubric into popups of up to 5 inputs. Scores come first, then the verdict and note."""
    scores = [RatingItem("score", c) for c in rating.categories]
    extras = []
    if rating.verdict_label:
        extras.append(RatingItem("verdict"))
    if rating.note_label:
        extras.append(RatingItem("note"))
    pages = [scores[i : i + MAX_PAGE_ITEMS] for i in range(0, len(scores), MAX_PAGE_ITEMS)] or [[]]
    if len(pages[-1]) + len(extras) <= MAX_PAGE_ITEMS:
        pages[-1] += extras
    else:
        pages.append(extras)
    return pages


# Discord side


@dataclass
class RatingDraft:
    """A rating being filled in across the form's pages."""

    forums: ForumsContext
    template: Template
    post: Post
    previous: MemberRating | None
    scores: dict[str, int] = field(default_factory=dict)
    verdict: str | None = None
    note: str | None = None

    @property
    def rating(self) -> Rating:
        if self.template.rating is None:
            raise RuntimeError(f"{self.template.id} has no rating rubric")
        return self.template.rating


class RatingView(ui.View):
    """The buttons on the bot's message under each post. One registered instance serves every post."""

    def __init__(self, forums: ForumsContext, template: Template) -> None:
        super().__init__(timeout=None)
        self.forums = forums
        self.template = template
        rating = template.rating
        if rating is None:
            return

        rate: ui.Button = ui.Button(
            label=rating.button_label,
            emoji=rating.emoji,
            style=discord.ButtonStyle.primary,
            custom_id=RATE_BUTTON_PREFIX + template.id,
        )
        rate.callback = self.rate
        self.add_item(rate)

        if rating.guide:
            guide: ui.Button = ui.Button(
                label="How to review",
                emoji="📘",
                style=discord.ButtonStyle.secondary,
                custom_id=GUIDE_BUTTON_PREFIX + template.id,
            )
            guide.callback = self.show_guide
            self.add_item(guide)

    async def rate(self, interaction: discord.Interaction) -> None:
        post = await self.forums.storage.get_post(interaction.channel_id or 0)
        if self.template.rating is None or post is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ratings aren't available here.", ephemeral=True)
            return
        if interaction.user.id == post.reporter_id:
            await interaction.response.send_message(f"You can't rate your own {noun(self.template)}.", ephemeral=True)
            return
        if post.status in self.template.final_statuses:
            await interaction.response.send_message("This post is closed, so it can't be rated.", ephemeral=True)
            return

        previous = await self.forums.storage.get_rating(post.thread_id, interaction.user.id)
        draft = RatingDraft(self.forums, self.template, post, previous)
        await interaction.response.send_modal(RatingPage(draft, 0))

    async def show_guide(self, interaction: discord.Interaction) -> None:
        guide = self.template.rating.guide if self.template.rating else None
        await interaction.response.send_message(guide or "No guide for this form.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item, /) -> None:
        log.exception("Rating button failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)


class RatingPage(ui.Modal):
    def __init__(self, draft: RatingDraft, index: int) -> None:
        pages = rating_pages(draft.rating)
        title = f"Rate this {noun(draft.template)}"
        if len(pages) > 1:
            title += f" ({index + 1}/{len(pages)})"
        super().__init__(title=title[:45], timeout=FORM_TIMEOUT)
        self.draft = draft
        self.index = index
        self.is_last = index == len(pages) - 1
        self.inputs: list[tuple[RatingItem, ui.Item]] = []

        previous = draft.previous
        for item in pages[index]:
            if item.kind == "score" and item.category is not None:
                key = item.category.key
                chosen = draft.scores.get(key) or (previous.scores.get(key) if previous else None)
                component: ui.Item = ui.Select(
                    placeholder="Score out of 10",
                    options=[
                        discord.SelectOption(label=f"{n} / 10", value=str(n), default=n == chosen)
                        for n in range(MAX_CATEGORY_SCORE, 0, -1)
                    ],
                )
                label, description = item.category.label, item.category.description
            elif item.kind == "verdict":
                chosen_verdict = draft.verdict or (previous.verdict if previous else None)
                component = ui.Select(
                    options=[
                        discord.SelectOption(label=o, value=o, default=o == chosen_verdict)
                        for o in draft.rating.verdict_options
                    ],
                )
                label, description = draft.rating.verdict_label or "Verdict", None
            else:
                component = ui.TextInput(
                    style=discord.TextStyle.paragraph,
                    max_length=1000,
                    required=draft.rating.note_required,
                    placeholder=draft.rating.note_placeholder,
                    default=draft.note or (previous.note if previous else None),
                )
                label, description = draft.rating.note_label or "Note", None
            self.inputs.append((item, component))
            self.add_item(ui.Label(text=label, description=description, component=component))

    def _collect(self) -> None:
        for item, component in self.inputs:
            if isinstance(component, ui.Select) and component.values:
                if item.kind == "score" and item.category is not None:
                    self.draft.scores[item.category.key] = int(component.values[0])
                else:
                    self.draft.verdict = component.values[0]
            elif isinstance(component, ui.TextInput):
                self.draft.note = component.value.strip() or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self._collect()
        if not self.is_last:
            await self._reply(
                interaction, "Scores saved. One more step.", RatingContinueView(self.draft, self.index + 1)
            )
            return

        await self._reply(interaction, "Posting your rating…", None)
        reviewer = interaction.user
        if not isinstance(reviewer, discord.Member):
            return
        member = MemberRating(
            thread_id=self.draft.post.thread_id,
            rater_id=reviewer.id,
            scores=dict(self.draft.scores),
            verdict=self.draft.verdict,
            note=self.draft.note,
            message_id=self.draft.previous.message_id if self.draft.previous else None,
        )
        try:
            await publish_rating(self.draft.forums, self.draft.template, reviewer, member)
        except Exception:
            log.exception("Failed to post rating for %s", reviewer)
            await interaction.edit_original_response(content="Something went wrong posting your rating.")
            return
        verb = "updated" if self.draft.previous else "posted"
        await interaction.edit_original_response(
            content=f"Your rating was {verb}: **{member.total} / {self.draft.rating.max_score}**."
        )

    async def _reply(self, interaction: discord.Interaction, content: str, view: ui.View | None) -> None:
        # Page 1 opens from the post's button, so it starts a private message; later pages update it.
        if self.index == 0:
            if view is None:
                await interaction.response.send_message(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(content=content, view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        log.exception("Rating form failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong with the rating form.", ephemeral=True)


class RatingContinueView(ui.View):
    """Discord can't open a popup from another popup, so the pages are joined by a button."""

    def __init__(self, draft: RatingDraft, index: int) -> None:
        super().__init__(timeout=FORM_TIMEOUT)
        self.draft = draft
        self.index = index
        button: ui.Button = ui.Button(label="Continue", style=discord.ButtonStyle.success)
        button.callback = self.open_next
        self.add_item(button)

    async def open_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RatingPage(self.draft, self.index))


async def publish_rating(
    forums: ForumsContext, template: Template, reviewer: discord.Member, member: MemberRating
) -> MemberRating:
    """Post (or update) the rating in the thread, save it, and refresh the averages."""
    rating = template.rating
    if rating is None:
        raise RuntimeError(f"{template.id} has no rating rubric")
    thread = await fetch_thread(reviewer.guild, member.thread_id)
    binding = await forums.storage.get_binding(thread.parent_id) if thread.parent_id else None

    message_id = await _post_or_edit(
        forums, binding, thread, reviewer, render_rating(rating, member), member.message_id
    )
    saved = replace(member, message_id=message_id)
    await forums.storage.save_rating(saved)

    summary = await forums.storage.rating_summary(thread.id)
    await _refresh_scores(forums, template, thread, binding, summary)
    log.info("%s rated %s: %d/%d", reviewer, thread.name, saved.total, rating.max_score)
    return saved


async def _post_or_edit(
    forums: ForumsContext,
    binding: Binding | None,
    thread: discord.Thread,
    reviewer: discord.Member,
    content: str,
    previous_id: int | None,
) -> int:
    """Show the rating under the reviewer's name through the forum's webhook, or as the bot if that fails."""
    if binding and binding.webhook_url:
        webhook = discord.Webhook.from_url(binding.webhook_url, client=forums.bot)
        try:
            if previous_id:
                try:
                    await webhook.edit_message(
                        previous_id, content=content, thread=thread, allowed_mentions=NO_MENTIONS
                    )
                    return previous_id
                except discord.NotFound:
                    pass  # the old rating was deleted, so post a new one
            message = await webhook.send(
                content,
                username=webhook_username(reviewer.display_name),
                avatar_url=reviewer.display_avatar.url,
                thread=thread,
                allowed_mentions=NO_MENTIONS,
                wait=True,
            )
            return message.id
        except discord.HTTPException:
            log.exception("Webhook rating failed, posting as the bot instead")

    content = f"-# Rating by {reviewer.mention}\n{content}"
    if previous_id:
        try:
            await thread.get_partial_message(previous_id).edit(content=content, allowed_mentions=NO_MENTIONS)
            return previous_id
        except discord.HTTPException:
            pass
    return (await thread.send(content, allowed_mentions=NO_MENTIONS)).id


async def _refresh_scores(
    forums: ForumsContext, template: Template, thread: discord.Thread, binding: Binding | None, summary: RatingSummary
) -> None:
    """Update the average on the rating message and in the post's top line. Failures here are only logged."""
    rating = template.rating
    if rating is None:
        return

    message_id = await forums.storage.get_rating_message(thread.id)
    if message_id:
        try:
            await thread.get_partial_message(message_id).edit(content=summary_text(template, summary))
        except discord.HTTPException:
            log.warning("Could not update the rating message in %s", thread.name)

    try:
        starter = thread.starter_message or await thread.fetch_message(thread.id)
    except discord.HTTPException:
        return
    top_line, _, rest = starter.content.partition("\n")
    if not top_line.startswith("-# "):
        return
    new_top = with_score(top_line, rating, summary)
    if new_top == top_line:
        return
    content = f"{new_top}\n{rest}" if rest else new_top
    try:
        if starter.webhook_id and binding and binding.webhook_url:
            webhook = discord.Webhook.from_url(binding.webhook_url, client=forums.bot)
            await webhook.edit_message(starter.id, content=content, thread=thread, allowed_mentions=NO_MENTIONS)
        elif forums.bot.user and starter.author.id == forums.bot.user.id:
            await starter.edit(content=content, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        log.warning("Could not update the score in %s", thread.name)


async def offer_rating(forums: ForumsContext, template: Template, thread: discord.Thread) -> None:
    """Post the rating buttons under a new post, if its form has a rubric. A failure never blocks the post."""
    if template.rating is None:
        return
    try:
        message = await thread.send(summary_text(template, RatingSummary()), view=RatingView(forums, template))
        await forums.storage.set_rating_message(thread.id, message.id)
    except discord.HTTPException:
        log.warning("Could not add the rating buttons in %s", thread.name)


class Leaderboard(commands.Cog):
    def __init__(self, forums: ForumsContext) -> None:
        self.forums = forums

    @app_commands.command(name="leaderboard", description="Show the top-rated posts for a form")
    @app_commands.describe(form="Which form's leaderboard to show")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction, form: str) -> None:
        template = self.forums.templates.get(form)
        rating = template.rating if template else None
        if template is None or rating is None:
            await interaction.response.send_message("Pick a form from the list.", ephemeral=True)
            return

        ranked = await self.forums.storage.top_rated(template.id, rating.leaderboard_min)
        if not ranked:
            await interaction.response.send_message(
                f"No {noun(template)}s have {rating.leaderboard_min}+ ratings yet. Go rate some!", ephemeral=True
            )
            return

        lines = []
        for place, post in enumerate(ranked, start=1):
            badge = MEDALS[place - 1] if place <= len(MEDALS) else f"**{place}.**"
            plural = "rating" if post.count == 1 else "ratings"
            lines.append(
                f"{badge} <#{post.thread_id}>\n"
                f"-# {rating.emoji} {format_score(post.average)}/{rating.max_score} · "
                f"{post.count} {plural} · by <@{post.reporter_id}>"
            )
        embed = discord.Embed(
            title=f"{rating.emoji} Top {template.name}s", description="\n".join(lines), color=template.color
        )
        embed.set_footer(text=f"Posts need at least {rating.leaderboard_min} ratings to be ranked.")
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_MENTIONS)

    @leaderboard.autocomplete("form")
    async def form_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        current = current.lower()
        return [
            app_commands.Choice(name=t.name, value=t.id)
            for t in self.forums.templates.values()
            if t.rating is not None and current in t.name.lower()
        ][:25]
