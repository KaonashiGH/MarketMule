"""Stalls: each member gets one permanent shop post, written and laid out however they like.

Members make their own post in the stalls channel and edit it whenever they want. Replies are off in
the channel, so nobody can bump a stall by commenting. The bot handles the rest: one stall per member,
CSFloat and Steam links only, a Bump button (with a cooldown) under each stall, and closing stalls
that go quiet.

Files:
    settings.py     wording, the template members copy, allowed links and limits (templates/stalls/stalls.json)
    storage.py      tables: stall channels, stalls
    render.py       link checks, timing and message text (no Discord calls)
    service.py      taking on, removing, bumping, closing and reopening stalls on Discord
    views.py        Get the template, Bump, Close stall and Reopen stall buttons
    cog.py          /stalls commands, the events that watch members' posts, and the upkeep loop
    context.py      shared state used across the feature
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cog import Stalls
from .context import StallContext
from .settings import load_settings
from .storage import StallStorage
from .views import ControlView, ReopenView, StallPanel

if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule


async def setup(bot: MarketMule) -> None:
    settings = load_settings(bot.config.templates_dir / "stalls" / "stalls.json")
    storage = StallStorage(bot.db)
    await storage.migrate()

    ctx = StallContext(bot=bot, storage=storage, settings=settings)
    # Keeps the buttons on existing posts working after restarts.
    bot.add_view(StallPanel(ctx))
    bot.add_view(ControlView(ctx))
    bot.add_view(ReopenView(ctx))
    await bot.add_cog(Stalls(ctx))
