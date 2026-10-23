import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from discord import ui

from marketmule.core.config import PROJECT_ROOT
from marketmule.core.database import Database
from marketmule.features.forums.ratings import (
    GUIDE_BUTTON_PREFIX,
    RATE_BUTTON_PREFIX,
    RatingView,
    format_score,
    rating_pages,
    render_rating,
    summary_text,
    with_score,
)
from marketmule.features.forums.storage import MIGRATIONS, ForumStorage, MemberRating, Post, RatingSummary
from marketmule.features.forums.templates import TemplateError, load_templates

FORUM_TEMPLATES = PROJECT_ROOT / "templates" / "forums"
TEMPLATES = load_templates(FORUM_TEMPLATES)
LOADOUT = TEMPLATES["loadout"]
TRADEUP = TEMPLATES["tradeup"]


def rating_of(template_id: str) -> Any:
    rating = TEMPLATES[template_id].rating
    assert rating is not None
    return rating


# Text


def test_scores_drop_needless_decimals() -> None:
    assert format_score(32.0) == "32"
    assert format_score(31.5) == "31.5"
    assert format_score(31.25) == "31.2"


def test_summary_line() -> None:
    assert "No ratings yet" in summary_text(LOADOUT, RatingSummary())
    assert "**31.5 / 40** average from 4 ratings" in summary_text(LOADOUT, RatingSummary(31.5, 4))
    assert "from 1 rating." in summary_text(LOADOUT, RatingSummary(30, 1))


def test_score_added_then_replaced_in_top_line() -> None:
    rating = rating_of("loadout")
    once = with_score("-# 🎨 Loadout · $1,500", rating, RatingSummary(30, 1))
    assert once == "-# 🎨 Loadout · $1,500 · ⭐ 30/40"
    assert with_score(once, rating, RatingSummary(31.5, 2)) == "-# 🎨 Loadout · $1,500 · ⭐ 31.5/40"


def test_loadout_rating_follows_the_rubric() -> None:
    member = MemberRating(1, 2, {"color": 8, "budget": 7, "originality": 9, "stickers": 6}, note="Swap the gloves.")
    assert render_rating(rating_of("loadout"), member) == (
        "> **LOADOUT LAB RATING**\n"
        "> Color Cohesion: **8**/10\n"
        "> Budget Efficiency: **7**/10\n"
        "> Originality: **9**/10\n"
        "> Sticker Synergy: **6**/10\n"
        ">\n"
        "> **Final Score: 30 / 40**\n"
        "> **Critic's Note:** Swap the gloves."
    )


def test_tradeup_rating_has_verdict_and_multiline_note() -> None:
    member = MemberRating(
        1, 2, {"float": 9, "risk": 8, "filler": 7, "hype": 10}, verdict="SEND IT", note="Great math.\nGo for it."
    )
    text = render_rating(rating_of("tradeup"), member)
    assert "> **Final Score: 34 / 40**" in text
    assert "> **Critic's Verdict:** SEND IT" in text
    assert text.endswith("> **Critic's Note:** Great math.\n> Go for it.")


def test_rating_form_pages() -> None:
    assert [[i.kind for i in page] for page in rating_pages(rating_of("loadout"))] == [
        ["score", "score", "score", "score", "note"]
    ]
    assert [[i.kind for i in page] for page in rating_pages(rating_of("tradeup"))] == [
        ["score", "score", "score", "score"],
        ["verdict", "note"],
    ]


def test_rating_buttons() -> None:
    async def make() -> tuple[RatingView, RatingView, RatingView]:
        return (
            RatingView(cast(Any, None), LOADOUT),
            RatingView(cast(Any, None), TRADEUP),
            RatingView(cast(Any, None), TEMPLATES["bug_report"]),
        )

    loadout, tradeup, bug = asyncio.run(make())
    assert [b.custom_id for b in loadout.children] == [RATE_BUTTON_PREFIX + "loadout"]
    assert [b.custom_id for b in tradeup.children] == [RATE_BUTTON_PREFIX + "tradeup", GUIDE_BUTTON_PREFIX + "tradeup"]
    assert bug.children == []  # no rubric, no buttons
    assert loadout.is_persistent() and tradeup.is_persistent()
    assert all(isinstance(b, ui.Button) for b in tradeup.children)


# Template checks


def write_rating(directory: Path, **changes: object) -> None:
    template = json.loads((FORUM_TEMPLATES / "loadout.json").read_text(encoding="utf-8"))
    template["rating"].update(changes)
    (directory / "test.json").write_text(json.dumps(template), encoding="utf-8")


def test_rejects_duplicate_category_keys(tmp_path: Path) -> None:
    write_rating(tmp_path, categories=[{"key": "a", "label": "A"}, {"key": "a", "label": "B"}])
    with pytest.raises(TemplateError, match="duplicate category key 'a'"):
        load_templates(tmp_path)


def test_rejects_one_option_verdict(tmp_path: Path) -> None:
    write_rating(tmp_path, verdict={"label": "Verdict", "options": ["YES"]})
    with pytest.raises(TemplateError, match="verdict needs 2"):
        load_templates(tmp_path)


# Storage


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def fresh_storage(tmp_path: Path) -> tuple[Database, ForumStorage]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    storage = ForumStorage(db)
    await storage.migrate()
    return db, storage


def test_rating_again_replaces_the_old_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        db, storage = await fresh_storage(tmp_path)
        await storage.save_rating(MemberRating(1, 50, {"a": 5, "b": 5}, message_id=900))
        await storage.save_rating(MemberRating(1, 50, {"a": 9, "b": 9}, message_id=900))
        await storage.save_rating(MemberRating(1, 60, {"a": 6, "b": 6}))

        mine = await storage.get_rating(1, 50)
        assert mine is not None and mine.total == 18 and mine.message_id == 900
        assert await storage.rating_summary(1) == RatingSummary(average=15.0, count=2)
        assert await storage.rating_summary(2) == RatingSummary()
        await db.close()

    run(scenario())


def test_leaderboard_ranks_by_average_and_needs_enough_ratings(tmp_path: Path) -> None:
    async def scenario() -> None:
        db, storage = await fresh_storage(tmp_path)
        for thread_id, reporter in ((1, 100), (2, 200), (3, 300)):
            await storage.add_post(Post(thread_id, 10, "loadout", thread_id, reporter, "Open"))
        await storage.add_post(Post(4, 11, "tradeup", 1, 400, "Open"))

        ratings = {1: [30, 32], 2: [38, 36], 3: [40], 4: [40, 40]}  # post 3 has too few, post 4 is a trade-up
        for thread_id, totals in ratings.items():
            for rater, total in enumerate(totals):
                await storage.save_rating(MemberRating(thread_id, rater, {"a": total}))

        ranked = await storage.top_rated("loadout", min_ratings=2)
        assert [(p.thread_id, p.average, p.count, p.reporter_id) for p in ranked] == [
            (2, 37.0, 2, 200),
            (1, 31.0, 2, 100),
        ]
        await db.close()

    run(scenario())


def test_existing_posts_survive_the_ratings_upgrade(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        await db.migrate("forums", MIGRATIONS[:2])  # the database as it was before ratings
        storage = ForumStorage(db)
        await storage.add_post(Post(1, 10, "loadout", 1, 100, "Open"))

        await storage.migrate()
        assert await storage.get_post(1) == Post(1, 10, "loadout", 1, 100, "Open")
        assert await storage.get_rating_message(1) is None
        await storage.set_rating_message(1, 555)
        assert await storage.get_rating_message(1) == 555
        await db.close()

    run(scenario())
