"""Form templates, loaded from templates/forums/*.json and checked against Discord's limits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Matches {key} placeholders in title_format and section bodies.
PLACEHOLDER = re.compile(r"\{(\w+)\}")

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

FIELD_KINDS = frozenset({"text", "select", "file", "note"})


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
    heading: str
    body: str


@dataclass(frozen=True)
class Panel:
    thread_name: str
    title: str
    text: str
    button_label: str
    button_style: str = "primary"


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

    @property
    def fields(self) -> list[Field]:
        return [f for page in self.pages for f in page.fields if f.kind != "note"]

    @property
    def tag_fields(self) -> list[Field]:
        """Dropdowns whose answers are applied as forum tags."""
        return [f for f in self.fields if f.kind == "select" and f.tag]

    @property
    def initial_status(self) -> str:
        return self.statuses[0]

    def forum_tags(self) -> list[tuple[str, bool]]:
        """Every tag the forum needs, as (name, moderated). Status tags are staff-only."""
        tags = [(self.type_tag, False)]
        tags += [(option, False) for f in self.tag_fields for option in f.options]
        tags += [(status, True) for status in self.statuses]
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
    errors += [f"{where} uses {{{key}}}, but no question has that key" for where, key in used if key not in known]

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
