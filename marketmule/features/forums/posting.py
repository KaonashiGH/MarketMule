"""Turning a completed form into a forum post."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from .storage import Binding, Post
from .tags import find_tag, find_tags
from .templates import MAX_POST_TAGS, PLACEHOLDER, Section, Template

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 2000
MAX_THREAD_NAME = 100


@dataclass
class Submission:
    """A member's answers, collected across the form's pages."""

    template: Template
    binding: Binding
    author: discord.Member
    text: dict[str, str] = field(default_factory=dict)
    choices: dict[str, list[str]] = field(default_factory=dict)
    attachments: dict[str, list[discord.Attachment]] = field(default_factory=dict)

    def tag_names(self) -> list[str]:
        names = [self.template.type_tag]
        for f in self.template.tag_fields:
            names += self.choices.get(f.key, [])
        return names

    def answers(self) -> dict[str, str]:
        """Every answer as text. Unanswered questions are empty strings."""
        values = _Answers({key: value.strip() for key, value in self.text.items()})
        values.update({key: ", ".join(picked) for key, picked in self.choices.items()})
        return values


class _Answers(dict[str, str]):
    """Questions the member skipped read as empty instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return ""


def render(submission: Submission, *, number: int, author_mention: str) -> tuple[str, list[str]]:
    """Build the post title and its body, split into messages that fit Discord's length limit.

    The body is a small summary line, one heading per answered section, and a credit footer.
    Lines and sections whose questions were all skipped are left out, so posts never show "N/A".
    """
    template = submission.template
    answers = submission.answers()
    title = template.title_format.format_map(answers)[:MAX_THREAD_NAME]

    blocks = [_header_line(template, answers)]
    blocks += [block for section in template.sections if (block := _render_section(section, answers))]
    blocks.append(f"-# {template.credit} {author_mention} · {template.id_prefix}-{number:04d}")
    return title, split_message(blocks)


def _header_line(template: Template, answers: dict[str, str]) -> str:
    """For example: -# 💡 Feature request · Trading · Website"""
    emoji = template.tag_emojis.get(template.type_tag, "")
    parts = [f"{emoji} {template.name}".strip(), *(answers[key] for key in template.header if answers[key])]
    return "-# " + " · ".join(parts)


def _render_section(section: Section, answers: dict[str, str]) -> str | None:
    """Render one section, dropping lines whose questions were all skipped. None if nothing is left."""
    lines = []
    for line in section.body.split("\n"):
        keys = PLACEHOLDER.findall(line)
        if keys and not any(answers[key] for key in keys):
            continue
        lines.append(line.format_map(answers))
    body = "\n".join(lines).strip()
    return f"### {section.heading}\n{body}" if body else None


def split_message(blocks: list[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Pack blocks into as few messages as possible without splitting a block unless it alone is too long."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        while len(block) > limit:
            cut = max(block.rfind("\n", 0, limit), block.rfind(" ", 0, limit))
            if cut < limit // 2:
                cut = limit
            if current:
                messages.append(current)
                current = ""
            messages.append(block[:cut].rstrip())
            block = block[cut:].lstrip()
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


def webhook_username(display_name: str) -> str:
    """Discord rejects webhook names that contain 'discord' or 'clyde'."""
    cleaned = re.sub(r"(?i)discord|clyde", "", display_name).strip()
    return (cleaned or "Member")[:80]


async def publish(forums: ForumsContext, submission: Submission) -> discord.Thread:
    """Create the forum post, apply its tags and status, and record it."""
    template, author = submission.template, submission.author
    forum = await _fetch_forum(author.guild, submission.binding.channel_id)

    files = await _download(submission.attachments)
    number = await forums.storage.next_post_number(forum.id, template.id)
    tags = find_tags(forum, submission.tag_names())[: MAX_POST_TAGS - 1]  # leave room for the status tag

    thread: discord.Thread | None = None
    if submission.binding.webhook_url:
        try:
            thread = await _post_as_author(forums, submission, files, tags, number)
        except discord.HTTPException:
            log.exception("Webhook post failed, posting as the bot instead")
            for file in files:
                file.reset()
    if thread is None:
        thread = await _post_as_bot(forum, submission, files, tags, number)

    status_tag = find_tag(forum, template.initial_status)
    if status_tag is not None:
        thread = await thread.edit(applied_tags=[*thread.applied_tags[: MAX_POST_TAGS - 1], status_tag])

    for emoji in template.reactions:
        try:
            await thread.get_partial_message(thread.id).add_reaction(emoji)
        except discord.HTTPException:
            log.warning("Could not add reaction %s in %s (missing Add Reactions?)", emoji, forum.name)

    await forums.storage.add_post(
        Post(
            thread_id=thread.id,
            channel_id=forum.id,
            template_id=template.id,
            number=number,
            reporter_id=author.id,
            status=template.initial_status,
        )
    )
    log.info("Created %s-%04d for %s in #%s", template.id_prefix, number, author, forum.name)
    return thread


async def _post_as_author(
    forums: ForumsContext,
    submission: Submission,
    files: list[discord.File],
    tags: list[discord.ForumTag],
    number: int,
) -> discord.Thread:
    """Post through the forum's webhook, showing the member's name and avatar."""
    author = submission.author
    title, messages = render(submission, number=number, author_mention=author.mention)
    webhook = discord.Webhook.from_url(submission.binding.webhook_url or "", client=forums.bot)
    identity = {"username": webhook_username(author.display_name), "avatar_url": author.display_avatar.url}
    mentions = _mention_only(author)

    first = await webhook.send(
        messages[0],
        files=files if len(messages) == 1 else [],
        thread_name=title,
        applied_tags=tags,
        allowed_mentions=mentions,
        wait=True,
        **identity,
    )
    # A forum post's starter message has the same ID as the post itself.
    for index, content in enumerate(messages[1:], start=1):
        await webhook.send(
            content,
            files=files if index == len(messages) - 1 else [],
            thread=discord.Object(first.id),
            allowed_mentions=mentions,
            **identity,
        )
    return await fetch_thread(author.guild, first.id)


async def _post_as_bot(
    forum: discord.ForumChannel,
    submission: Submission,
    files: list[discord.File],
    tags: list[discord.ForumTag],
    number: int,
) -> discord.Thread:
    """Fallback when the webhook is missing or fails: the bot posts and credits the member."""
    author = submission.author
    title, messages = render(submission, number=number, author_mention=author.mention)
    mentions = _mention_only(author)

    created = await forum.create_thread(
        name=title,
        content=messages[0],
        files=files if len(messages) == 1 else [],
        applied_tags=tags,
        allowed_mentions=mentions,
    )
    for index, content in enumerate(messages[1:], start=1):
        await created.thread.send(content, files=files if index == len(messages) - 1 else [], allowed_mentions=mentions)
    return created.thread


def _mention_only(member: discord.Member) -> discord.AllowedMentions:
    """Mentioning the author adds them to the post so they're notified of replies."""
    return discord.AllowedMentions(everyone=False, roles=False, users=[member])


async def _download(attachments: dict[str, list[discord.Attachment]]) -> list[discord.File]:
    files: list[discord.File] = []
    for attachment in (a for group in attachments.values() for a in group):
        try:
            files.append(await attachment.to_file())
        except discord.HTTPException:
            log.warning("Could not download attachment %s", attachment.filename)
    return files


async def _fetch_forum(guild: discord.Guild, channel_id: int) -> discord.ForumChannel:
    channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    if not isinstance(channel, discord.ForumChannel):
        raise RuntimeError(f"Channel {channel_id} is not a forum")
    return channel


async def fetch_thread(guild: discord.Guild, thread_id: int) -> discord.Thread:
    thread = guild.get_thread(thread_id) or await guild.fetch_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        raise RuntimeError(f"Channel {thread_id} is not a thread")
    return thread