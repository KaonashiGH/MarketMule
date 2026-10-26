"""Turning a completed form into a forum post."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import ui

from marketmule.core.icons import Icons

from .storage import Binding, Post
from .tags import find_tag, find_tags
from .templates import MAX_POST_TAGS, PLACEHOLDER, STATUS_KEY, Section, Template

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 2000
MAX_THREAD_NAME = 100
CARD_TEXT_LIMIT = 4000  # all the text in one card message, added up
GALLERY_LIMIT = 10
NO_ICONS = Icons(Path())  # strips :mm_name: when no icons are available, e.g. in thread titles


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


def render(
    submission: Submission, *, number: int, author_mention: str, icons: Icons = NO_ICONS
) -> tuple[str, list[str]]:
    """Build the post title and its body, split into messages that fit Discord's length limit.

    The body is a small summary line, one heading per answered section, and a credit footer.
    Lines and sections whose questions were all skipped are left out, so posts never show "N/A".
    """
    template = submission.template
    answers = submission.answers()
    answers[STATUS_KEY] = template.status_label(template.initial_status)
    title = render_title(template.title_format, answers)

    rendered = [(section, block) for section in template.sections if (block := _render_section(section, answers))]
    if template.card_title:
        # Same start as a card: the member's headline, then the small line (the icon moves up to the headline).
        headline = template.card_title.format_map(answers).strip()
        top = f"## {_form_icon(template, icons)} {headline}".replace("##  ", "## ")
        blocks = [top + "\n" + _header_line(template, answers, icons, plain=True)]
    else:
        blocks = [_header_line(template, answers, icons)]
    # Discord puts a message's pictures and buttons after all its text, so a credit at the end would sit above
    # them. Instead it goes on the small line at the top: "-# Price Check · Tradable now · Posted by @x · PC-0015".
    blocks[0] += " · " + _credit(template, author_mention, number).removeprefix("-# ")
    blocks += [block for section, block in rendered if not section.after_credit]
    blocks += [block for section, block in rendered if section.after_credit]  # e.g. a link, near the bottom
    # One line apart: Discord already adds space above each ### heading, so a blank line would double it.
    return title, split_message([icons.apply(block) for block in blocks], separator="\n")


TITLE_SEPARATOR = " · "


def retitle(name: str, template: Template, old_status: str, new_status: str) -> str | None:
    """The post's title with its status swapped, e.g. "🔵 Needs pricing - AK" → "🟢 Priced - AK".

    None if the form's title doesn't show the status, or the old status isn't in the title (renamed by hand).
    """
    if "{" + STATUS_KEY + "}" not in template.title_format:
        return None
    old, new = template.status_label(old_status), template.status_label(new_status)
    if old not in name:
        return None
    return name.replace(old, new, 1)[:MAX_THREAD_NAME]


def render_title(title_format: str, answers: dict[str, str]) -> str:
    """Fill in the title. Pieces separated by " · " are dropped when all their answers were skipped."""
    pieces = title_format.split(TITLE_SEPARATOR)
    kept = [p for p in pieces if not (keys := PLACEHOLDER.findall(p)) or any(answers[k] for k in keys)]
    title = TITLE_SEPARATOR.join(p.format_map(answers) for p in kept)
    return NO_ICONS.apply(title)[:MAX_THREAD_NAME]  # thread names can't show custom icons


def _form_icon(template: Template, icons: Icons) -> str:
    """The form's own icon if it's uploaded, otherwise the emoji on its tag."""
    return (icons.text(template.icon) if template.icon else "") or template.tag_emojis.get(template.type_tag, "")


def _header_line(template: Template, answers: dict[str, str], icons: Icons, *, plain: bool = False) -> str:
    """For example: -# 💡 Feature request · Trading · Website. Cards already show the icon, so plain leaves it off."""
    name = template.name if plain else f"{_form_icon(template, icons)} {template.name}".strip()
    parts = [name, *(answers[key] for key in template.header if answers[key])]
    return "-# " + " · ".join(parts)


def _credit(template: Template, author_mention: str, number: int) -> str:
    """The small footer, e.g. -# Posted by @someone · PC-0001"""
    return f"-# {template.credit} {author_mention} · {template.id_prefix}-{number:04d}"


def _render_section(section: Section, answers: dict[str, str]) -> str | None:
    """Render one section, dropping lines whose questions were all skipped. None if nothing is left."""
    lines = []
    for line in section.body.split("\n"):
        keys = PLACEHOLDER.findall(line)
        if keys and not any(answers[key] for key in keys):
            continue
        if line.startswith(">") and keys:
            # A quoted answer that runs over several lines stays inside the quote.
            filled = line.format_map(answers)
            lines += ["> " + part if not part.startswith(">") else part for part in filled.split("\n")]
            continue
        lines.append(line.format_map(answers))
    body = "\n".join(lines).strip()
    if not body:
        return None
    return f"### {section.heading}\n{body}" if section.heading else body


def split_message(blocks: list[str], limit: int = MESSAGE_LIMIT, separator: str = "\n\n") -> list[str]:
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
        candidate = f"{current}{separator}{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


def render_card(
    submission: Submission,
    *,
    number: int,
    author_mention: str,
    icons: Icons = NO_ICONS,
    files: list[discord.File] | None = None,
) -> ui.LayoutView | None:
    """Build a card post: a colored box with a title, one block per section, dividers, pictures and a link button.

    Returns None if the answers are too long for one card, caller will post plain text instead.
    """
    template = submission.template
    answers = submission.answers()
    title = (template.card_title or template.title_format).format_map(answers).strip()
    thumbnail = icons.url(template.icon) if template.thumbnail else None
    icon = "" if thumbnail else _form_icon(template, icons)  # with a thumbnail, the icon sits there instead

    heading = icons.apply(f"## {icon} {title}").replace("##  ", "## ")
    texts: list[str] = [heading + "\n" + _header_line(template, answers, icons, plain=True)]
    banner_name = template.banner_filename if template.banner_on_posts else None
    banner = next((f for f in files or [] if banner_name and f.filename == banner_name), None)
    children: list[ui.Item] = [ui.MediaGallery(discord.MediaGalleryItem(banner))] if banner else []
    top = ui.TextDisplay(texts[0])
    children.append(ui.Section(top, accessory=ui.Thumbnail(thumbnail)) if thumbnail else top)
    first_section = len(children)
    for section in template.sections:
        if section.after_credit or (block := _render_section(section, answers)) is None:
            continue
        if section.divider or len(children) == first_section:
            children.append(ui.Separator())
        texts.append(icons.apply(block))
        children.append(ui.TextDisplay(texts[-1]))

    uploads = [f for f in files or [] if f is not banner]
    images = [f for f in uploads if _is_image(f.filename)]
    others = [f for f in uploads if not _is_image(f.filename)]
    if images:
        children.append(ui.Separator())
        children += [ui.MediaGallery(*(discord.MediaGalleryItem(f) for f in chunk)) for chunk in _chunks(images)]
    children += [ui.File(f) for f in others]

    for section in template.sections:
        if section.after_credit and (block := _render_section(section, answers)) is not None:
            texts.append(icons.apply(block))
            children.append(ui.TextDisplay(texts[-1]))
    link = _link_button(template, answers)
    if link is not None:
        children.append(ui.ActionRow(link))
    texts.append(_credit(template, author_mention, number))  # lowest priority, so always last
    children.append(ui.TextDisplay(texts[-1]))

    if sum(len(t) for t in texts) > CARD_TEXT_LIMIT:
        return None
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*children, accent_colour=template.color))
    return view


def _link_button(template: Template, answers: dict[str, str]) -> ui.Button | None:
    if template.link_button is None:
        return None
    url = answers[template.link_button.key].strip().strip("<>")
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url  # e.g. steamcommunity.com/id/name still gets its button
    if not re.fullmatch(r"https?://\S+", url) or len(url) > 512:
        return None  # if not a usable link
    return ui.Button(label=template.link_button.label, url=url, style=discord.ButtonStyle.link)


def _is_image(filename: str) -> bool:
    return filename.lower().rsplit(".", 1)[-1] in {"png", "jpg", "jpeg", "gif", "webp"}


def _chunks(files: list[discord.File]) -> Iterator[list[discord.File]]:
    for start in range(0, len(files), GALLERY_LIMIT):
        yield files[start : start + GALLERY_LIMIT]


def webhook_username(display_name: str) -> str:
    """Discord rejects webhook names that contain 'discord' or 'clyde'."""
    cleaned = re.sub(r"(?i)discord|clyde", "", display_name).strip()
    return (cleaned or "Member")[:80]


async def publish(forums: ForumsContext, submission: Submission) -> discord.Thread:
    """Create the forum post, apply its tags and status, and record it."""
    template, author = submission.template, submission.author
    forum = await _fetch_forum(author.guild, submission.binding.channel_id)

    files = await _download(submission.attachments)
    if template.is_card and template.banner_on_posts:
        config = getattr(forums.bot, "config", None)
        banner = banner_file(getattr(config, "banners_dir", None), template)
        files = [banner, *files] if banner else files
    number = await forums.storage.next_post_number(forum.id, template.id)
    icons: Icons = getattr(forums.bot, "icons", NO_ICONS)
    tags = find_tags(forum, submission.tag_names())[: MAX_POST_TAGS - 1]  # leave room for the status tag

    thread: discord.Thread | None = None
    if submission.binding.webhook_url:
        try:
            thread = await _post_as_author(forums, submission, files, tags, number, icons)
        except discord.HTTPException:
            log.exception("Webhook post failed, posting as the bot instead")
            for file in files:
                file.reset()
    if thread is None:
        thread = await _post_as_bot(forum, submission, files, tags, number, icons)

    status_tag = find_tag(forum, template.initial_status)
    if status_tag is not None:
        thread = await thread.edit(applied_tags=[status_tag, *thread.applied_tags[: MAX_POST_TAGS - 1]])

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
    icons: Icons,
) -> discord.Thread:
    """Post through the forum's webhook, showing the member's name and avatar."""
    author = submission.author
    title, messages = render(submission, number=number, author_mention=author.mention, icons=icons)
    webhook = discord.Webhook.from_url(submission.binding.webhook_url or "", client=forums.bot)
    identity = {"username": webhook_username(author.display_name), "avatar_url": author.display_avatar.url}
    mentions = _mention_only(author)

    card = _card(submission, number, author, icons, files)
    if card is not None:
        sent = await webhook.send(
            view=card,
            files=files,
            thread_name=title,
            applied_tags=tags,
            allowed_mentions=mentions,
            wait=True,
            **identity,
        )
        return await fetch_thread(author.guild, sent.id)

    button = link_view(submission)  # e.g. View on CSFloat, under the last message like on a card
    last = len(messages) - 1
    first = await webhook.send(
        messages[0],
        files=files if last == 0 else [],
        thread_name=title,
        applied_tags=tags,
        allowed_mentions=mentions,
        wait=True,
        **({"view": button} if button and last == 0 else {}),
        **identity,
    )
    # A forum post's starter message has the same ID as the post itself.
    for index, content in enumerate(messages[1:], start=1):
        await webhook.send(
            content,
            files=files if index == last else [],
            thread=discord.Object(first.id),
            allowed_mentions=mentions,
            **({"view": button} if button and index == last else {}),
            **identity,
        )
    return await fetch_thread(author.guild, first.id)


async def _post_as_bot(
    forum: discord.ForumChannel,
    submission: Submission,
    files: list[discord.File],
    tags: list[discord.ForumTag],
    number: int,
    icons: Icons,
) -> discord.Thread:
    """Fallback when the webhook is missing or fails: the bot posts and credits the member."""
    author = submission.author
    title, messages = render(submission, number=number, author_mention=author.mention, icons=icons)
    mentions = _mention_only(author)

    card = _card(submission, number, author, icons, files)
    if card is not None:
        created = await forum.create_thread(
            name=title, view=card, files=files, applied_tags=tags, allowed_mentions=mentions
        )
        return created.thread

    button = link_view(submission)
    last = len(messages) - 1
    created = await forum.create_thread(
        name=title,
        content=messages[0],
        files=files if last == 0 else [],
        applied_tags=tags,
        allowed_mentions=mentions,
        **({"view": button} if button and last == 0 else {}),
    )
    for index, content in enumerate(messages[1:], start=1):
        await created.thread.send(
            content,
            files=files if index == last else [],
            allowed_mentions=mentions,
            **({"view": button} if button and index == last else {}),
        )
    return created.thread


def banner_file(banners_dir: Path | None, template: Template) -> discord.File | None:
    """The form's banner image, ready to attach. None if the form has none or the file is missing."""
    if not template.banner or banners_dir is None or template.banner_filename is None:
        return None
    path = banners_dir / f"{template.banner}.png"
    if not path.is_file():
        log.warning("Banner %s not found for %s", path.name, template.id)
        return None
    return discord.File(path, filename=template.banner_filename)


def link_view(submission: Submission) -> ui.View | None:
    """Text posts: the same link button a card has (e.g. View on CSFloat), as a normal button row."""
    button = _link_button(submission.template, submission.answers())
    if button is None:
        return None
    view = ui.View(timeout=None)
    view.add_item(button)
    return view


def _card(
    submission: Submission, number: int, author: discord.Member, icons: Icons, files: list[discord.File]
) -> ui.LayoutView | None:
    """The card version of the post, or None for text-style forms (or answers too long for a card)."""
    if not submission.template.is_card:
        return None
    card = render_card(submission, number=number, author_mention=author.mention, icons=icons, files=files)
    if card is None:
        log.warning("Answers too long for a card; posting %s as text", submission.template.id)
    return card


def _mention_only(member: discord.Member) -> discord.AllowedMentions:
    """Mentioning the author adds them to the post so they're notified of replies."""
    return discord.AllowedMentions(everyone=False, roles=False, users=[member])


async def _download(attachments: dict[str, list[discord.Attachment]]) -> list[discord.File]:
    files: list[discord.File] = []
    for index, attachment in enumerate(a for group in attachments.values() for a in group):
        try:
            file = await attachment.to_file()
            # Cards point at files by name, so names must be unique and safe in a link.
            file.filename = f"{index + 1}-" + re.sub(r"[^\w.-]", "_", file.filename)
            files.append(file)
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
