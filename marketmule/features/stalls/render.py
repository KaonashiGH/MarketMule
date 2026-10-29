"""Text and checks for stalls. No Discord calls, so all of it is easy to test.

A stall is the member's own forum post. Under it sits one bot message, the control message:
"Stall of @x · last bumped 2 hours ago" with the owner's Bump and Close stall buttons.
A bump posts the control message again and deletes the old one: forums are sorted by newest
message, so that moves the stall to the top. Members can't reply in stalls, so nothing else can bump them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from .settings import StallSettings
from .storage import Stall

HOUR = 60 * 60
DAY = 24 * HOUR
MESSAGE_LIMIT = 2000

# Links Discord makes clickable: anything starting http(s)://, plus www. and Discord invites without it.
LINK = re.compile(r"(?i)\bhttps?://[^\s<>()\[\]]+|\bwww\.[^\s<>()\[\]]+|\b(?:discord\.gg|discord\.com/invite)/[\w-]+")


# Links


def find_links(text: str) -> list[str]:
    links: list[str] = []
    for match in LINK.finditer(text or ""):
        link = match.group(0).rstrip(".,!?;:'\"*_~`")
        if link not in links:
            links.append(link)
    return links


def link_host(link: str) -> str:
    """The site a link really goes to. csfloat.com@evil.site goes to evil.site, so that's what counts."""
    url = link if "://" in link else "https://" + link
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_allowed(link: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = link_host(link)
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)


def bad_links(text: str, allowed_hosts: tuple[str, ...]) -> list[str]:
    """Links in the text that go anywhere other than the allowed sites (CSFloat and Steam by default)."""
    return [link for link in find_links(text) if not is_allowed(link, allowed_hosts)]


# Timing


def wait_left(last: int, now: int, hours: float) -> int:
    """Seconds until something with an `hours` cooldown can happen again. 0 means now."""
    return max(0, int(last + hours * HOUR) - now)


def is_inactive(stall: Stall, now: int, days: int) -> bool:
    return not stall.is_closed and now - stall.active_at >= days * DAY


# Messages


def control_text(settings: StallSettings, stall: Stall) -> str:
    """The bot's message under a stall. The time is a Discord timestamp, so it counts up by itself."""
    control, limits = settings.control, settings.limits
    lines = [control.text.format(owner=f"<@{stall.owner_id}>", bumped=f"<t:{stall.bumped_at}:R>")]
    if control.owner_hint:
        hint = control.owner_hint.format(
            manual_hours=hours_text(limits.manual_bump_hours), edit_hours=hours_text(limits.edit_bump_hours)
        )
        lines.append(hint)
    return "\n".join(lines)


@dataclass(frozen=True)
class Picture:
    """A picture under a stall: the file on the bot's message, and the owner's description of it."""

    filename: str
    description: str = ""


def picture_caption(settings: StallSettings, number: int, description: str) -> str:
    return settings.control.picture_caption.format(
        number=number, description=description.strip() or settings.control.no_description
    )


def picture_name(taken: Iterable[str], original: str) -> str:
    """A short, unique file name for a new picture (pic1.png, pic2.jpg, ...). The layout refers to it by name."""
    taken = set(taken)
    extension = original.rsplit(".", 1)[-1].lower() if "." in original else "png"
    number = 1
    while any(name.startswith(f"pic{number}.") for name in taken):
        number += 1
    return f"pic{number}.{extension}"


def closed_text(settings: StallSettings, reason: str) -> str:
    return settings.control.closed_text.format(reason=reason)


def removal_message(settings: StallSettings, *, title: str, channel: str, reason: str, content: str) -> list[str]:
    """The DM a member gets when their post is removed: why, then their text so nothing they wrote is lost."""
    removed = settings.removed
    first = removed.intro.format(title=title, channel=channel, reason=reason)
    if not content.strip():
        return [first[:MESSAGE_LIMIT]]
    safe = content.replace("```", "'''")  # can't close the code block early
    room = MESSAGE_LIMIT - len("```\n\n```")
    copy = f"```\n{safe[:room]}\n```"
    return [f"{first}\n{removed.copy}"[:MESSAGE_LIMIT], copy]


def template_message(settings: StallSettings) -> str:
    return f"{settings.template_intro}\n```\n{settings.template}\n```"


def hours_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
