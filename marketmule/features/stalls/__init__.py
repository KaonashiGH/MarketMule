"""Stalls: each member get one persistant shop post in a forum, and can manage it with guided buttons

The bot owns the layout so members wont be able to edit directly, they will be able to use modals and other formed messages to add, edit, sell and remove items through the forms. 
Bot ill repost listing as correctly/updated version. Stalls are read only for members, only the bot can post in them, this also allows the bot to control when/how the shop is bumped
to the top of the forum channel. This should also limit the amount of "scripting" allowed for posts keeping them user managed

Files:
    Settings.py     Woring limits, templets/stalls
    Storage.py      Table
    render.py       building stall, link and trade-hold math 
    service.py      reposting after edits, bumping, tagging
    views.py        Open stall panel, control panel for owner, forms
    cog.py          Stall setup, keeps stalls open, listings, maintanance loop
    context/py      Shared state used across the feature
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cog import Stalls
from .context import StallContext
from .settings import load_settings
from .storage import StallStorage
from .views import ControlView, Reopenview, StallPanel




if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule


async def setup(bot: MarketMule) -> None:
    settings = load_settings(bot.config.templates_dir / "stalls" / "stalls.json")
    storage = StallStorage(bot.db)
    await storage.migrate()


    ctx = StallsContext(bot=bot, storage=storage, settings=settings)
    #button persistance
    bot.add_view(StallPanel(ctx))
    bot.add_view(ControlView(ctx))
    bot.add_view(ReopenView(ctx))
    await bot.add_cog(Stalls(ctx))