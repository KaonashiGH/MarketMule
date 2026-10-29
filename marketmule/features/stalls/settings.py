"""Stall wording and limits, loaded from templates/stalls/stalls.json and checked at startup."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_MESSAGE = 2000
MAX_TEXT_DISPLAY = 4000
MAX_BUTTON_LABEL = 80
MAX_TAG_NAME = 20
TAG_KEYS = ("open", "closed")


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Panel:
    thread_name: str
    title: str
    text: str
    button_label: str


@dataclass(frozen=True)
class ControlText:
    text: str
    owner_hint: str
    bump_button: str
    close_button: str
    reopen_button: str
    closed_text: str
    add_picture_button: str = "Add picture"
    remove_picture_button: str = "Remove picture"
    picture_form_title: str = "Add a picture"
    picture_label: str = "Picture"
    picture_hint: str = "PNG, JPG, GIF or WEBP"
    description_label: str = "Description"
    description_placeholder: str = "e.g. AK-47 | Redline (FT 0.21) · $25"
    picture_caption: str = "**{number}.** {description}"
    no_description: str = "Picture"
    remove_prompt: str = "Pick the pictures to remove:"


@dataclass(frozen=True)
class RemovedText:
    duplicate: str
    bad_link: str
    too_soon: str
    intro: str
    copy: str


@dataclass(frozen=True)
class Limits:
    manual_bump_hours: float = 12
    edit_bump_hours: float = 6
    inactive_days: int = 30
    max_pictures: int = 10


@dataclass(frozen=True)
class StallSettings:
    panel: Panel
    template: str
    template_intro: str
    control: ControlText
    removed: RemovedText
    allowed_link_hosts: tuple[str, ...]
    tags: dict[str, str]  # "open" / "closed" → the Discord tag's name
    tag_emojis: dict[str, str]  # tag name → emoji
    limits: Limits
    banner: str | None = None
    icon: str | None = None
    color: int = 0x237BFF

    @property
    def banner_filename(self) -> str | None:
        return f"banner-stalls-{self.banner}.png" if self.banner else None

    def tag_name(self, status: str) -> str:
        return self.tags[status]


def load_settings(path: Path) -> StallSettings:
    try:
        settings = _parse(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise SettingsError(f"{path} is missing") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise SettingsError(f"{path.name}: invalid settings ({exc!r})") from exc
    errors = validate(settings)
    if errors:
        raise SettingsError(f"{path.name}:\n  " + "\n  ".join(errors))
    return settings


def _parse(raw: dict[str, Any]) -> StallSettings:
    return StallSettings(
        panel=Panel(**raw["panel"]),
        template=raw["template"],
        template_intro=raw["template_intro"],
        control=ControlText(**raw["control"]),
        removed=RemovedText(**raw["removed"]),
        allowed_link_hosts=tuple(h.lower().strip() for h in raw["allowed_link_hosts"]),
        tags={key: raw["tags"][key] for key in TAG_KEYS},
        tag_emojis=dict(raw.get("tag_emojis", {})),
        limits=Limits(**raw.get("limits", {})),
        banner=raw.get("banner"),
        icon=raw.get("icon"),
        color=int(str(raw.get("color", "#237bff")).lstrip("#"), 16),
    )


def validate(settings: StallSettings) -> list[str]:
    errors: list[str] = []
    if len(f"## {settings.panel.title}\n{settings.panel.text}") > MAX_TEXT_DISPLAY:
        errors.append(f"panel title + text is over {MAX_TEXT_DISPLAY} characters")
    # The template is sent as a code block after its intro, in one message.
    if len(settings.template_intro) + len(settings.template) + 10 > MAX_MESSAGE:
        errors.append(f"template + template_intro is over {MAX_MESSAGE} characters")
    if "```" in settings.template:
        errors.append("template can't contain ``` (it's shown inside a code block)")
    labels = (
        settings.panel.button_label,
        settings.control.bump_button,
        settings.control.close_button,
        settings.control.reopen_button,
        settings.control.add_picture_button,
        settings.control.remove_picture_button,
    )
    for label in labels:
        if not 1 <= len(label) <= MAX_BUTTON_LABEL:
            errors.append(f"button label {label!r} needs 1-{MAX_BUTTON_LABEL} characters")
    for text, needed in (
        (settings.control.text, ("{owner}", "{bumped}")),
        (settings.control.closed_text, ("{reason}",)),
        (settings.control.picture_caption, ("{number}", "{description}")),
        (settings.removed.duplicate, ("{link}",)),
        (settings.removed.bad_link, ("{links}",)),
        (settings.removed.too_soon, ("{when}",)),
        (settings.removed.intro, ("{reason}",)),
    ):
        errors += [f"{text[:40]!r}… must contain {key}" for key in needed if key not in text]

    if not settings.allowed_link_hosts:
        errors.append('allowed_link_hosts needs at least one site, e.g. "csfloat.com"')
    for host in settings.allowed_link_hosts:
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
            errors.append(f'allowed_link_hosts: {host!r} should be a plain site name like "csfloat.com"')

    names = list(settings.tags.values())
    for name in names:
        if not 1 <= len(name) <= MAX_TAG_NAME:
            errors.append(f"tag name needs 1-{MAX_TAG_NAME} characters: {name!r}")
    if len({n.lower() for n in names}) != len(names):
        errors.append("tag names must all be different")
    unknown = set(settings.tag_emojis) - set(names)
    if unknown:
        errors.append(f"tag_emojis names {sorted(unknown)} don't match any tag in tags")

    limits = settings.limits
    if limits.manual_bump_hours < 0 or limits.edit_bump_hours < 0:
        errors.append("bump hours can't be negative")
    if limits.inactive_days < 1:
        errors.append("inactive_days must be at least 1")
    if not 1 <= limits.max_pictures <= 10:
        errors.append("max_pictures must be 1-10 (Discord allows 10 files per message)")
    control = settings.control
    for name in ("picture_form_title", "picture_label", "description_label"):
        if not 1 <= len(getattr(control, name)) <= 45:
            errors.append(f"{name} needs 1-45 characters")
    for name in ("picture_hint", "description_placeholder", "remove_prompt", "no_description"):
        if len(getattr(control, name)) > 100:
            errors.append(f"{name} must be 100 characters or less")
    if settings.banner is not None and not re.fullmatch(r"[\w-]+", settings.banner):
        errors.append('banner should be a file name without .png, like "stalls"')
    return errors
