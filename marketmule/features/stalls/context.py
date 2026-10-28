"""State shared by the stalls feature's commands, buttons, events and upkeep loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule

    from .settings import StallSettings
    from .storage import StallStorage


@dataclass
class StallContext:
    bot: MarketMule
    storage: StallStorage
    settings: StallSettings
    # One lock per stall, so two quick clicks (or a click and an edit) can't bump it twice.
    _locks: dict[int, asyncio.Lock] = field(default_factory=dict)

    def lock(self, thread_id: int) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())
