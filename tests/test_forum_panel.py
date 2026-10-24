import asyncio
from typing import Any, cast

import pytest
from discord import ui

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.forms import START_BUTTON_PREFIX, FormPanel, panel_thread_name
from marketmule.features.forums.status import CLOSE_BUTTON_PREFIX, AuthorCloseView
from marketmule.features.forums.templates import load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")


def build(*template_ids: str) -> FormPanel:
    async def make() -> FormPanel:  # views need a running event loop
        return FormPanel(cast(Any, None), [TEMPLATES[t] for t in template_ids])

    return asyncio.run(make())


def items_of(panel: FormPanel, kind: type) -> list:
    return [item for item in panel.walk_children() if isinstance(item, kind)]


needs_loadout_and_tradeup = pytest.mark.skipif(
    not {"loadout", "tradeup"} <= set(TEMPLATES), reason="loadout or tradeup is drafted (.draft)"
)


@needs_loadout_and_tradeup
def test_two_forms_share_one_card_with_a_divider() -> None:
    panel = build("loadout", "tradeup")

    assert len(items_of(panel, ui.Container)) == 1
    assert len(items_of(panel, ui.Separator)) == 1
    buttons = items_of(panel, ui.Button)
    assert [b.custom_id for b in buttons] == [START_BUTTON_PREFIX + "loadout", START_BUTTON_PREFIX + "tradeup"]
    assert [b.label for b in buttons] == ["Post a loadout", "Post a trade-up"]


def test_single_form_has_no_divider() -> None:
    panel = build("bug_report")
    assert items_of(panel, ui.Separator) == []
    assert len(items_of(panel, ui.Button)) == 1


def test_panel_survives_restarts() -> None:
    assert build(*TEMPLATES).is_persistent()


@needs_loadout_and_tradeup
def test_thread_names() -> None:
    assert panel_thread_name([TEMPLATES["bug_report"]]) == "Start here: Report a bug"
    assert panel_thread_name([TEMPLATES["loadout"], TEMPLATES["tradeup"]]) == "Start here: Loadout & Trade-up"


def test_price_check_close_button() -> None:
    async def make() -> tuple[AuthorCloseView, AuthorCloseView]:
        template = TEMPLATES["price_check"]
        return AuthorCloseView(cast(Any, None), template), AuthorCloseView(cast(Any, None), template, disabled=True)

    live, used = asyncio.run(make())
    [button] = items_of(live, ui.Button)
    assert button.label == "Mark as priced"
    assert button.custom_id == CLOSE_BUTTON_PREFIX + "price_check"
    assert live.is_persistent()
    assert items_of(used, ui.Button)[0].disabled


def test_forms_without_close_button_get_none() -> None:
    async def make() -> AuthorCloseView:
        return AuthorCloseView(cast(Any, None), TEMPLATES["bug_report"])

    assert items_of(asyncio.run(make()), ui.Button) == []


def test_banner_sits_above_its_form_when_attached() -> None:
    async def make() -> tuple[FormPanel, FormPanel]:
        forms = [TEMPLATES["price_check"]]
        return FormPanel(cast(Any, None), forms, banners={"banner-price_check.png"}), FormPanel(cast(Any, None), forms)

    with_banner, without = asyncio.run(make())
    (card,) = with_banner.children
    first = card.children[0]
    assert isinstance(first, ui.MediaGallery)
    assert first.items[0].media.url == "attachment://banner-price_check.png"
    assert not items_of(without, ui.MediaGallery)  # no file attached, so no broken image
