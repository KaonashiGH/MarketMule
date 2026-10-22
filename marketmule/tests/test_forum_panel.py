import asyncio
from typing import Any, cast

from discord import ui

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.forms import START_BUTTON_PREFIX, FormPanel, panel_thread_name
from marketmule.features.forums.templates import load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")


def build(*template_ids: str) -> FormPanel:
    async def make() -> FormPanel:  # views need a running event loop
        return FormPanel(cast(Any, None), [TEMPLATES[t] for t in template_ids])

    return asyncio.run(make())


def items_of(panel: FormPanel, kind: type) -> list:
    return [item for item in panel.walk_children() if isinstance(item, kind)]


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


def test_thread_names() -> None:
    assert panel_thread_name([TEMPLATES["bug_report"]]) == "Start here: Report a bug"
    assert panel_thread_name([TEMPLATES["loadout"], TEMPLATES["tradeup"]]) == "Start here: Loadout & Trade-up"
