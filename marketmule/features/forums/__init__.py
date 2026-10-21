"""Forums: members fill in a form and the bot publishes a standardized forum post.

Used for bug reports and feature requests. Each forum channel is connected to one
template from templates/forums/. Staff manage each post's status and tags.

Files:
    templates.py  template model and validation
    storage.py    this feature's tables
    context.py    shared state passed to commands and views
    forms.py      member-facing Start button and form pages
    posting.py    rendering and publishing posts
    tags.py       forum tag helpers
    admin.py      /forums setup, remove, list
    staff.py      Manage post tool
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .admin import ForumAdmin
from .context import ForumsContext
from .forms import StartView
from .staff import ForumStaff
from .storage import ForumStorage
from .templates import load_templates

if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule


async def setup(bot: MarketMule) -> None:
    templates = load_templates(bot.config.templates_dir / "forums")
    storage = ForumStorage(bot.db)
    await storage.migrate()

    forums = ForumsContext(bot=bot, storage=storage, templates=templates)
    bot.add_view(StartView(forums))  # Keeps Start buttons on existing pinned posts working after restarts.
    await bot.add_cog(ForumAdmin(forums))
    await bot.add_cog(ForumStaff(forums))
