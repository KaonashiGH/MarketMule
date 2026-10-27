"""Form templates, loaded from templates/forums/*.json and checked against Discord's limits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Matches {key} placeholders in title_format and section bodies.
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# title_format may also use {status}: the post's status with its emoji, kept up to date, e.g. "🔵 Needs pricing".
STATUS_KEY = "status"

MAX_PAGE_ITEMS = 5
MAX_MODAL_TITLE = 45
MAX_LABEL = 45
MAX_DESCRIPTION = 100
MAX_PLACEHOLDER = 100
MAX_SELECT_OPTIONS = 25
MAX_UPLOADS = 10
MAX_TAG_NAME = 20
MAX_FORUM_TAGS = 20
MAX_POST_TAGS = 5
MAX_BUTTON_LABEL = 80
MAX_MESSAGE = 2000
MAX_CATEGORY_SCORE = 10
MAX_COMMAND_DESCRIPTION = 100
COMMAND_NAME = re.compile(r"[a-z0-9_-]{1,32}")
LONGEST_MENTION = "<@12345678901234567890>"

FIELD_KINDS = frozenset({"text", "select", "file", "note"})
POST_STYLES = frozenset({"text", "card"})


class TemplateError(Exception):
    pass


@dataclass(frozen=True)
class Field:
    kind: str
    key: str = ""
    label: str = ""
    description: str | None = None
    placeholder: str | None = None
    required: bool = True
    paragraph: bool = False
    max_length: int | None = None
    options: tuple[str, ...] = ()
    max_values: int = 1
    tag: bool = False
    text: str = ""


@dataclass(frozen=True)
class Page:
    name: str
    title: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Section:
    """One part of a post. Leave heading empty for no heading. after_credit puts it below the footer.

    divider draws a line above the section (card posts only).
    """

    heading: str
    body: str
    after_credit: bool = False
    divider: bool = False


@dataclass(frozen=True)
class LinkButton:
    """Card posts: turns one answer (a link) into a button, e.g. "View on CSFloat"."""

    key: str
    label: str


@dataclass(frozen=True)
class Panel:
    thread_name: str
    title: str
    text: str
    button_label: str
    button_style: str = "primary"


@dataclass(frozen=True)
class AuthorClose:
    """Lets the person who posted close their own post with a button, e.g. "Mark as priced"."""

    status: str
    button_label: str
    text: str


@dataclass(frozen=True)
class StaffShortcut:
    """A staff slash command that sets a post's status and posts a ready-made message, e.g. /nab.

    In the message, {user} becomes a ping of the person who posted. The optional button opens a link.
    """

    command: str
    description: str
    status: str
    message: str
    button_label: str | None = None
    button_url: str | None = None


@dataclass(frozen=True)
class RatingCategory:
    """One line of a rating rubric, scored 1-10."""

    key: str
    label: str
    description: str | None = None


@dataclass(frozen=True)
class Rating:
    """Lets members rate a post with a rubric, e.g. the "Loadout Critic" rubric.

    Each category is scored 1-10 from a dropdown. A verdict (pick one option) and a written note
    are optional extras. A guide adds a "How to review" button that shows reviewers some tips.
    """

    title: str
    button_label: str
    categories: tuple[RatingCategory, ...]
    emoji: str = "⭐"
    verdict_label: str | None = None
    verdict_options: tuple[str, ...] = ()
    note_label: str | None = None
    note_placeholder: str | None = None
    note_required: bool = True
    guide: str | None = None
    leaderboard_min: int = 3

    @property
    def max_score(self) -> int:
        return MAX_CATEGORY_SCORE * len(self.categories)


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    type_tag: str
    id_prefix: str
    color: int
    title_format: str
    panel: Panel
    pages: tuple[Page, ...]
    sections: tuple[Section, ...]
    statuses: tuple[str, ...]
    final_statuses: frozenset[str]
    tag_emojis: dict[str, str]
    reactions: tuple[str, ...]
    header: tuple[str, ...] = ()
    credit: str = "Submitted by"
    author_close: AuthorClose | None = None
    staff_shortcuts: tuple[StaffShortcut, ...] = ()
    rating: Rating | None = None
    style: str = "text"
    icon: str | None = None
    card_title: str | None = None
    link_button: LinkButton | None = None
    thumbnail: bool = False
    banner: str | None = None
    banner_on_posts: bool = False

    @property
    def banner_filename(self) -> str | None:
        """The name the banner is attached under, e.g. banner-price_check.png."""
        return f"banner-{self.id}.png" if self.banner else None

    @property
    def is_card(self) -> bool:
        return self.style == "card"

    @property
    def fields(self) -> list[Field]:
        return [f for page in self.pages for f in page.fields if f.kind != "note"]

    @property
    def tag_fields(self) -> list[Field]:
        """Dropdowns whose answers are applied as forum tags."""
        return [f for f in self.fields if f.kind == "select" and f.tag]

    def status_label(self, status: str) -> str:
        """How a status reads in the title, e.g. "🔵 Needs pricing"."""
        return f"{self.tag_emojis.get(status, '')} {status}".strip()

    def shortcut(self, command: str) -> StaffShortcut | None:
        return next((s for s in self.staff_shortcuts if s.command == command), None)

    @property
    def initial_status(self) -> str:
        return self.statuses[0]

    def forum_tags(self) -> list[tuple[str, bool]]:
        """Every tag the forum needs, as (name, moderated). Status tags are staff-only."""

        """Status come first in setup because setup create tags in this order, discord shows a posts tags oldest first. Create first and repost all when status changes to maintain this as the first  """

        tags= [(status, True) for status in self.statuses]
        tags += [(self.type_tag, False)]
        tags += [(option, False) for f in self.tag_fields for option in f.options]
        return tags 

def load_templates(directory: Path) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            template = _parse_template(json.loads(path.read_text(encoding="utf-8")))
        except (KeyError, TypeError, ValueError) as exc:
            raise TemplateError(f"{path.name}: invalid template ({exc!r})") from exc

        errors = _validate(template)
        if errors:
            raise TemplateError(f"{path.name}:\n  " + "\n  ".join(errors))
        if template.id in templates:
            raise TemplateError(f"{path.name}: duplicate template id {template.id!r}")
        templates[template.id] = template

    if not templates:
        raise TemplateError(f"No templates found in {directory}")
    return templates


def _parse_field(raw: dict[str, Any]) -> Field:
    return Field(
        kind=raw.get("type", "text"),
        key=raw.get("key", ""),
        label=raw.get("label", ""),
        description=raw.get("description"),
        placeholder=raw.get("placeholder"),
        required=raw.get("required", True),
        paragraph=raw.get("style") == "paragraph",
        max_length=raw.get("max_length"),
        options=tuple(raw.get("options", ())),
        max_values=raw.get("max", 1),
        tag=raw.get("tag", False),
        text=raw.get("text", ""),
    )


def _parse_template(raw: dict[str, Any]) -> Template:
    return Template(
        id=raw["id"],
        name=raw["name"],
        type_tag=raw["type_tag"],
        id_prefix=raw["id_prefix"],
        color=int(raw.get("color", "#5865f2").lstrip("#"), 16),
        title_format=raw["title_format"],
        panel=Panel(**raw["panel"]),
        pages=tuple(
            Page(name=p["name"], title=p["title"], fields=tuple(_parse_field(f) for f in p["fields"]))
            for p in raw["pages"]
        ),
        sections=tuple(Section(**s) for s in raw["sections"]),
        statuses=tuple(raw["statuses"]),
        final_statuses=frozenset(raw.get("final_statuses", ())),
        tag_emojis=dict(raw.get("tag_emojis", {})),
        reactions=tuple(raw.get("reactions", ())),
        header=tuple(raw.get("header", ())),
        credit=raw.get("credit", "Submitted by"),
        author_close=AuthorClose(**raw["author_close"]) if raw.get("author_close") else None,
        staff_shortcuts=tuple(StaffShortcut(**s) for s in raw.get("staff_shortcuts", ())),
        rating=_parse_rating(raw["rating"]) if raw.get("rating") else None,
        style=raw.get("style", "text"),
        icon=raw.get("icon"),
        card_title=raw.get("card_title"),
        link_button=LinkButton(**raw["link_button"]) if raw.get("link_button") else None,
        thumbnail=raw.get("thumbnail", False),
        banner=raw.get("banner"),
        banner_on_posts=raw.get("banner_on_posts", False),
    )


def _parse_rating(raw: dict[str, Any]) -> Rating:
    verdict = raw.get("verdict") or {}
    note = raw.get("note") or {}
    return Rating(
        title=raw["title"],
        button_label=raw["button_label"],
        categories=tuple(RatingCategory(**c) for c in raw["categories"]),
        emoji=raw.get("emoji", "⭐"),
        verdict_label=verdict.get("label"),
        verdict_options=tuple(verdict.get("options", ())),
        note_label=note.get("label"),
        note_placeholder=note.get("placeholder"),
        note_required=note.get("required", True),
        guide=raw.get("guide"),
        leaderboard_min=raw.get("leaderboard_min", 3),
    )


def _validate(template: Template) -> list[str]:
    errors: list[str] = []

    if not template.pages:
        errors.append("needs at least one page")
    if not template.statuses:
        errors.append("needs at least one status")

    for page in template.pages:
        if len(page.title) > MAX_MODAL_TITLE:
            errors.append(f"page title over {MAX_MODAL_TITLE} characters: {page.title!r}")
        if not 1 <= len(page.fields) <= MAX_PAGE_ITEMS:
            errors.append(f"page {page.name!r} has {len(page.fields)} items (1-{MAX_PAGE_ITEMS} allowed)")
        for field in page.fields:
            errors += _validate_field(field)

    keys = [f.key for f in template.fields]
    errors += [f"duplicate field key {key!r}" for key in sorted({k for k in keys if keys.count(k) > 1})]

    tag_names = [name for name, _ in template.forum_tags()]
    errors += [f"tag name over {MAX_TAG_NAME} characters: {name!r}" for name in tag_names if len(name) > MAX_TAG_NAME]
    if len({name.lower() for name in tag_names}) > MAX_FORUM_TAGS:
        errors.append(f"needs more than {MAX_FORUM_TAGS} forum tags")

    tags_per_post = 2 + sum(f.max_values for f in template.tag_fields)  # type + status + picks
    if tags_per_post > MAX_POST_TAGS:
        errors.append(f"a post could receive {tags_per_post} tags ({MAX_POST_TAGS} allowed)")

    # Catch typos: every {key} and header entry must match a question's key.
    known = set(keys)
    used = [("title_format", k) for k in PLACEHOLDER.findall(template.title_format)]
    used += [(f"section {s.heading!r}", k) for s in template.sections for k in PLACEHOLDER.findall(s.body)]
    used += [("header", k) for k in template.header]
    used += [("card_title", k) for k in PLACEHOLDER.findall(template.card_title or "")]
    if template.link_button is not None:
        used.append(("link_button", template.link_button.key))
    known.add(STATUS_KEY)  # {status} is filled in by the bot, not by a question
    errors += [f"{where} uses {{{key}}}, but no question has that key" for where, key in used if key not in known]
    if STATUS_KEY in keys:
        errors.append(f"no question can use the key {STATUS_KEY!r}: it's reserved for the post's status")

    if template.style not in POST_STYLES:
        errors.append(f"style must be one of {sorted(POST_STYLES)}, not {template.style!r}")
    if template.banner is not None and not re.fullmatch(r"[\w-]+", template.banner):
        errors.append(f'banner should be a file name without .png, like "pricecheck", not {template.banner!r}')
    if template.link_button is not None and not 1 <= len(template.link_button.label) <= MAX_BUTTON_LABEL:
        errors.append(f"link_button label needs 1-{MAX_BUTTON_LABEL} characters")

    close = template.author_close
    if close is not None:
        if close.status not in template.statuses:
            errors.append(f"author_close status {close.status!r} is not listed in statuses")
        if not 1 <= len(close.button_label) <= MAX_BUTTON_LABEL:
            errors.append(f"author_close button_label needs 1-{MAX_BUTTON_LABEL} characters")
        if not 1 <= len(close.text) <= MAX_MESSAGE:
            errors.append(f"author_close text needs 1-{MAX_MESSAGE} characters")

    errors += _validate_shortcuts(template)

    if template.rating is not None:
        errors += _validate_rating(template.rating)

    unknown = template.final_statuses - set(template.statuses)
    if unknown:
        errors.append(f"final_statuses not listed in statuses: {sorted(unknown)}")

    return errors


def _validate_field(field: Field) -> list[str]:
    if field.kind not in FIELD_KINDS:
        return [f"unknown field type {field.kind!r}"]
    if field.kind == "note":
        return [] if field.text else ["note is missing its text"]

    errors = []
    name = field.key or field.label or "<unnamed>"
    if not field.key:
        errors.append(f"field {name!r} is missing a key")
    if not field.label or len(field.label) > MAX_LABEL:
        errors.append(f"field {name!r} needs a label of 1-{MAX_LABEL} characters")
    if field.description and len(field.description) > MAX_DESCRIPTION:
        errors.append(f"field {name!r} description is over {MAX_DESCRIPTION} characters")
    if field.placeholder and len(field.placeholder) > MAX_PLACEHOLDER:
        errors.append(f"field {name!r} placeholder is over {MAX_PLACEHOLDER} characters")
    if field.kind == "select" and not 1 <= len(field.options) <= MAX_SELECT_OPTIONS:
        errors.append(f"dropdown {name!r} needs 1-{MAX_SELECT_OPTIONS} options")
    if field.kind == "file" and not 1 <= field.max_values <= MAX_UPLOADS:
        errors.append(f"upload {name!r} allows 1-{MAX_UPLOADS} files")
    return errors


def _validate_shortcuts(template: Template) -> list[str]:
    errors: list[str] = []
    names = [shortcut.command for shortcut in template.staff_shortcuts]
    errors += [f"staff shortcut /{name} is listed twice" for name in sorted({n for n in names if names.count(n) > 1})]
    for shortcut in template.staff_shortcuts:
        name = f"staff shortcut /{shortcut.command}"
        if not COMMAND_NAME.fullmatch(shortcut.command):
            errors.append(f"{name}: command must be 1-32 lowercase letters, numbers, - or _")
        if not 1 <= len(shortcut.description) <= MAX_COMMAND_DESCRIPTION:
            errors.append(f"{name}: description needs 1-{MAX_COMMAND_DESCRIPTION} characters")
        if shortcut.status not in template.statuses:
            errors.append(f"{name}: status {shortcut.status!r} is not listed in statuses")
        if not shortcut.message or len(shortcut.message.replace("{user}", LONGEST_MENTION)) > MAX_MESSAGE:
            errors.append(f"{name}: message needs 1-{MAX_MESSAGE} characters")
        if (shortcut.button_label is None) != (shortcut.button_url is None):
            errors.append(f"{name}: button_label and button_url go together")
        if shortcut.button_label is not None and not 1 <= len(shortcut.button_label) <= MAX_BUTTON_LABEL:
            errors.append(f"{name}: button_label needs 1-{MAX_BUTTON_LABEL} characters")
        if shortcut.button_url is not None and not shortcut.button_url.startswith("https://"):
            errors.append(f"{name}: button_url must start with https://")
    return errors


def _validate_rating(rating: Rating) -> list[str]:
    errors = []
    if not rating.categories:
        errors.append("rating needs at least one category")
    keys = [c.key for c in rating.categories]
    errors += [f"rating has duplicate category key {k!r}" for k in sorted({k for k in keys if keys.count(k) > 1})]
    for category in rating.categories:
        if not category.key:
            errors.append(f"rating category {category.label!r} is missing a key")
        if not 1 <= len(category.label) <= MAX_LABEL:
            errors.append(f"rating category {category.key!r} needs a label of 1-{MAX_LABEL} characters")
        if category.description and len(category.description) > MAX_DESCRIPTION:
            errors.append(f"rating category {category.key!r} description is over {MAX_DESCRIPTION} characters")
    if not 1 <= len(rating.button_label) <= MAX_BUTTON_LABEL:
        errors.append(f"rating button_label needs 1-{MAX_BUTTON_LABEL} characters")
    if rating.verdict_label is not None:
        if len(rating.verdict_label) > MAX_LABEL:
            errors.append(f"rating verdict label is over {MAX_LABEL} characters")
        if not 2 <= len(rating.verdict_options) <= MAX_SELECT_OPTIONS:
            errors.append(f"rating verdict needs 2-{MAX_SELECT_OPTIONS} options")
    if rating.note_label is not None and len(rating.note_label) > MAX_LABEL:
        errors.append(f"rating note label is over {MAX_LABEL} characters")
    if rating.note_placeholder and len(rating.note_placeholder) > MAX_PLACEHOLDER:
        errors.append(f"rating note placeholder is over {MAX_PLACEHOLDER} characters")
    if rating.guide and len(rating.guide) > MAX_MESSAGE:
        errors.append(f"rating guide is over {MAX_MESSAGE} characters")
    if rating.leaderboard_min < 1:
        errors.append("rating leaderboard_min must be at least 1")
    return errors
