from typing import Any, cast

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.posting import Submission, render, split_message, webhook_username
from marketmule.features.forums.storage import Binding
from marketmule.features.forums.templates import load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")
BINDING = Binding(channel_id=1, guild_id=1, template_id="bug_report", webhook_url=None, panel_thread_id=None)


def make_submission(**text: str) -> Submission:
    # render() never touches the author, so a placeholder object is enough here.
    return Submission(template=TEMPLATES["bug_report"], binding=BINDING, author=cast(Any, None), text=text)


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


def test_render_builds_title_and_footer() -> None:
    submission = make_submission(summary="Float overlaps name", description="It overlaps.")
    submission.choices["area"] = ["Marketplace"]

    title, messages = render(submission, number=7, credit="Submitted by someone", has_files=False)

    assert title == "[Marketplace] - Float overlaps name"
    body = "\n\n".join(messages)
    assert "It overlaps." in body
    assert "None provided." in body
    assert body.endswith("Submitted by someone · BUG-0007")


def test_render_fills_unanswered_fields_with_na() -> None:
    _, messages = render(make_submission(summary="x"), number=1, credit="c", has_files=True)
    body = "\n\n".join(messages)
    assert "**Trade ID or Item Float:** N/A" in body
    assert "See the images below." in body
