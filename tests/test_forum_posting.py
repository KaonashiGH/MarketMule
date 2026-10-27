import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from discord import ui

from marketmule.core.config import PROJECT_ROOT
from marketmule.core.icons import Icons
from marketmule.features.forums.posting import (
    NO_ICONS,
    Submission,
    _Answers,
    link_view,
    render,
    render_card,
    render_title,
    retitle,
    split_message,
    webhook_username,
)
from marketmule.features.forums.storage import Binding
from marketmule.features.forums.templates import PLACEHOLDER, load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")


def make_submission(template_id: str, choices: dict[str, list[str]], **text: str) -> Submission:
    # render() never touches the author, so a placeholder object is enough here.
    binding = Binding(channel_id=1, guild_id=1, template_ids=(template_id,), webhook_url=None, panel_thread_id=None)
    return Submission(
        template=TEMPLATES[template_id], binding=binding, author=cast(Any, None), text=text, choices=choices
    )


def body_of(submission: Submission, number: int = 7) -> tuple[str, str]:
    title, messages = render(submission, number=number, author_mention="@someone")
    return title, "\n\n".join(messages)


def test_split_keeps_short_blocks_together() -> None:
    assert split_message(["a", "b", "c"], limit=100) == ["a\n\nb\n\nc"]


def test_split_moves_block_to_next_message_when_full() -> None:
    assert split_message(["a" * 6, "b" * 6], limit=10) == ["a" * 6, "b" * 6]


def test_split_breaks_oversized_block_at_whitespace() -> None:
    block = " ".join(["word"] * 100)
    messages = split_message([block], limit=50)
    assert all(len(m) <= 50 for m in messages)
    assert " ".join(messages).split() == block.split()


def test_webhook_username_removes_blocked_words() -> None:
    assert webhook_username("DiscordFan") == "Fan"
    assert webhook_username("clyde") == "Member"
    assert len(webhook_username("x" * 200)) == 80


def test_feature_request_reads_like_a_post() -> None:
    submission = make_submission(
        "feature_request",
        {"area": ["Trading"], "platform": ["Website"], "category": []},
        summary="Bulk accept trades",
        description="Add a button to accept all pending trades.",
        why="Accepting 20 trades one by one takes forever.",
    )
    title, body = body_of(submission)

    assert title == "[🔵 Under review] - Trading · Bulk accept trades"
    assert body == (  # text style, like the price check
        "## 💡 Bulk accept trades\n-# Feature request · Trading · Website · Suggested by @someone · IDEA-0007\n"
        "### Description\n> Add a button to accept all pending trades.\n"
        "### Why it's needed\n> Accepting 20 trades one by one takes forever."
    )


def test_skipped_optional_questions_are_hidden() -> None:
    submission = make_submission(
        "bug_report",
        {"area": ["Marketplace"], "platform": ["Web Desktop"], "extension": ["No"], "category": []},
        summary="x",
        description="d",
        steps="s",
        expected_actual="e",
        browser_os="Chrome",
        steam_link="",
        trade_ref="12345",
    )
    _, body = body_of(submission)

    assert "N/A" not in body
    assert "**Trade ID / float:** 12345" in body
    assert "Steam profile" not in body  # skipped line is dropped, the rest of the section stays
    assert "Reported by @someone · BUG-0007" in body.split("\n")[1]  # credit sits on the small top line


def test_section_disappears_when_all_its_questions_are_skipped() -> None:
    submission = make_submission(
        "bug_report",
        {"area": ["Account"], "platform": ["Mobile App"], "extension": ["No"]},
        summary="x",
        description="d",
        steps="s",
        expected_actual="e",
        browser_os="iOS",
    )
    _, body = body_of(submission)
    assert "### Account & trade" not in body


class FakeEmoji:
    def __init__(self, name: str) -> None:
        self.name = name
        self.url = f"https://cdn.example/{name}.png"

    def __str__(self) -> str:
        return f"<:{self.name}:1>"


def icons_with(*names: str) -> Icons:
    icons = Icons(Path())
    icons.set(cast(Any, [FakeEmoji(n) for n in names]))
    return icons


def price_check(**changes: str) -> Submission:
    answers = dict(
        title="Tier 2 Blue Gem AK",
        item="AK-47 | Case Hardened",
        wear="Field-Tested 0.23456",
        pattern="",
        stickers="",
        question="Is this tier 2?\nHow much over market?",
        link="https://csfloat.com/item/123",
        hold="Tradable now",
    )
    answers.update(changes)
    return make_submission("price_check", {}, **answers)


def card_texts(submission: Submission, icons: Icons = NO_ICONS) -> tuple[list[str], list[Any]]:
    async def build() -> Any:
        return render_card(submission, number=1, author_mention="@someone", icons=icons)

    view = asyncio.run(build())
    assert view is not None
    (container,) = view.children
    texts = [c.content for c in container.walk_children() if isinstance(c, ui.TextDisplay)]
    return texts, list(container.children)


def test_price_check_card_uses_headers_and_quotes() -> None:
    texts, children = card_texts(price_check())
    assert texts[0] == "## 💰 Tier 2 Blue Gem AK\n-# Price Check"
    assert "### Item\n> AK-47 | Case Hardened" in texts
    # Every line of a long answer stays quoted.
    assert "### Question for the community\n> Is this tier 2?\n> How much over market?" in texts
    assert not any("Pattern" in t or "Stickers" in t for t in texts)  # skipped questions disappear
    assert texts[-1] == "-# Posted by @someone · PC-0001"
    assert isinstance(children[-2], ui.ActionRow)  # the link button sits above the small credit line
    (row,) = [c for c in children if isinstance(c, ui.ActionRow)]
    (button,) = row.children
    assert button.url == "https://csfloat.com/item/123" and button.label == "View on CSFloat"


def test_card_link_button_only_for_real_links() -> None:
    _, children = card_texts(price_check(link="not a link"))
    assert not any(isinstance(c, ui.ActionRow) for c in children)


def test_card_uses_uploaded_icons() -> None:
    submission = price_check(stickers="4x Kato")
    submission.template = replace(submission.template, thumbnail=False)  # icon goes in the title instead
    texts, _ = card_texts(submission, icons_with("mm_pricecheck", "mm_stickers"))
    assert texts[0].startswith("## <:mm_pricecheck:1> Tier 2 Blue Gem AK")


def test_card_too_long_falls_back_to_text() -> None:
    async def build() -> Any:
        long = "x" * 900
        submission = price_check(question=long, item=long, wear=long, pattern=long, stickers=long, title=long)
        return render_card(submission, number=1, author_mention="@someone")

    assert asyncio.run(build()) is None


def test_missing_icons_leave_no_gap() -> None:
    icons = icons_with("mm_rate")
    assert icons.apply("## :mm_pricecheck: Price check") == "## Price check"
    assert icons.apply(":mm_rate: Rate it") == "<:mm_rate:1> Rate it"
    assert icons.emoji("mm_bump", "🔼") == "🔼"
    assert render_title("[PC] :mm_pricecheck: {item}", _Answers(item="AK")) == "[PC] AK"  # thread names: plain text


def test_title_drops_skipped_pieces() -> None:
    answers = _Answers(item="AK-47 | Case Hardened", wear="FT 0.23", pattern="")
    assert (
        render_title("[Price Check] - {item} · {wear} · {pattern}", answers)
        == "[Price Check] - AK-47 | Case Hardened · FT 0.23"
    )


def test_title_is_cut_to_discord_limit() -> None:
    assert len(render_title("{name}", _Answers(name="x" * 300))) == 100


def test_thumbnail_shows_the_form_icon_in_the_corner() -> None:
    submission = price_check()
    submission.template = replace(submission.template, thumbnail=True)
    texts, children = card_texts(submission, icons_with("mm_pricecheck"))
    section = children[0]
    assert isinstance(section, ui.Section)
    assert section.accessory.media.url == "https://cdn.example/mm_pricecheck.png"
    assert texts[0].startswith("## Tier 2 Blue Gem AK")  # the icon moved to the corner, not repeated


def test_banner_on_posts_sits_above_everything() -> None:
    import io

    import discord

    submission = price_check()
    submission.template = replace(submission.template, banner="pricecheck", banner_on_posts=True)

    async def build() -> Any:
        banner = discord.File(io.BytesIO(b"x"), filename="banner-price_check.png")
        shot = discord.File(io.BytesIO(b"x"), filename="1-shot.png")
        return render_card(submission, number=1, author_mention="@someone", files=[banner, shot])

    (container,) = asyncio.run(build()).children
    galleries = [c for c in container.children if isinstance(c, ui.MediaGallery)]
    assert container.children[0] is galleries[0]
    assert [i.media.url for i in galleries[0].items] == ["attachment://banner-price_check.png"]
    assert [i.media.url for i in galleries[1].items] == ["attachment://1-shot.png"]  # banner isn't in the gallery


def test_price_check_as_text_keeps_headline_and_link() -> None:
    submission = price_check()
    submission.template = replace(submission.template, style="text")
    _, body = body_of(submission, number=8)
    assert body.startswith(
        "## 💰 Tier 2 Blue Gem AK\n-# Price Check · Posted by @someone · PC-0008\n### Item\n> AK-47"
    )
    assert "\n\n" not in body  # no blank lines: headings already add their own space
    assert body.endswith("> How much over market?")  # credit is up top, so nothing sits above the pictures

    async def button() -> Any:
        return link_view(submission)

    (link,) = asyncio.run(button()).children  # a real button under the post, the same as on a card
    assert link.label == "View on CSFloat" and link.url == "https://csfloat.com/item/123"


def test_title_shows_the_status_and_follows_it() -> None:
    template = TEMPLATES["price_check"]
    title, _ = body_of(price_check(), number=1)
    assert title == "[🔵 Needs Pricing] - AK-47 | Case Hardened · Field-Tested 0.23456"
    assert retitle(title, template, "Needs Pricing", "Priced") == (
        "[🟢 Priced] - AK-47 | Case Hardened · Field-Tested 0.23456"
    )
    assert retitle("Renamed by staff", template, "Needs pricing", "Priced") is None  # leaves custom titles alone


def test_bug_and_feature_titles_show_the_status_and_follow_it() -> None:
    bug = make_submission(
        "bug_report", {"area": ["Marketplace"], "platform": ["Web Desktop"]}, summary="Floats overlap names"
    )
    title, _ = body_of(bug)
    assert title == "[🔵 Open] - Marketplace · Floats overlap names"
    assert retitle(title, bug.template, "Open", "Fixed") == "[🟢 Fixed] - Marketplace · Floats overlap names"
    idea = make_submission("feature_request", {"area": ["Trading"]}, summary="Bulk accept")
    title, _ = body_of(idea)
    assert retitle(title, idea.template, "Under review", "Planned") == "[🟣 Planned] - Trading · Bulk accept"


def test_titles_are_never_cut_off() -> None:
    """The longest status plus full-length answers still fit Discord's 100-character post title, counted the
    way Discord counts (an emoji like 🔵 counts as 2). Price check is left out: its item name alone can be 100."""
    for template in TEMPLATES.values():
        if template.id == "price_check":
            continue
        fields = {f.key: f for f in template.fields}
        for status in template.statuses:
            answers = _Answers(status=template.status_label(status))
            for key in PLACEHOLDER.findall(template.title_format):
                if key in fields:
                    f = fields[key]
                    answers[key] = max(f.options, key=len) if f.options else "x" * (f.max_length or 100)
            title = template.title_format.format_map(answers)
            assert len(title.encode("utf-16-le")) // 2 <= 100, (template.id, status, title)


def test_bug_report_is_a_text_post_like_the_price_check() -> None:
    submission = make_submission(
        "bug_report",
        {"area": ["Marketplace"], "platform": ["Web Desktop"], "category": ["UI/UX"]},
        summary="Floats overlap names",
        description="Floats cover the names.",
        steps="1. Open inventory\n2. Scroll",
        expected_actual="Expected: under\nActual: over",
        browser_os="Chrome 141, Windows 11",
    )
    _, body = body_of(submission, number=3)
    assert body.startswith(
        "## 🐞 Floats overlap names\n"
        "-# Bug report · Marketplace · UI/UX · Reported by @someone · BUG-0003\n"
    )
    assert "### Steps to reproduce\n> 1. Open inventory\n> 2. Scroll\n" in body  # every line stays quoted
    # The skipped version line is dropped, and so is the whole skipped Account & trade section.
    assert body.endswith("### Setup\n> **Platform:** Web Desktop\n> **Browser & OS:** Chrome 141, Windows 11")
    assert "\n\n" not in body
