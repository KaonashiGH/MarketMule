"""State shared by the forums feature's commands, forms and posting code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule

    from .storage import ForumStorage
    from .templates import Template


@dataclass(frozen=True)
class ForumsContext:
    bot: MarketMule
    storage: ForumStorage
    templates: dict[str, Template]
