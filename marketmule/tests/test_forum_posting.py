from typing import Any, cast

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.posting import Submission, render, split_message, webhook_username
from marketmule.features.forums.storage import Binding
from marketmule.features.forums.templates import load_templates

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

    assert title == "[Trading] - Bulk accept trades"
    assert body == (
        "-# 💡 Feature request · Trading · Website\n\n"
        "### Description\nAdd a button to accept all pending trades.\n\n"
        "### Why it's needed\nAccepting 20 trades one by one takes forever.\n\n"
        "-# Suggested by @someone · IDEA-0007"
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
    assert body.endswith("Reported by @someone · BUG-0007")


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
