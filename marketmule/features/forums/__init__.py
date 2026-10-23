"""Forums: members fill in a form and the bot publishes a standardized forum post.

Used for bug reports, feature requests, loadouts and trade-ups. Each forum channel offers one or
more forms from templates/forums/, each with its own button on the channel's pinned post.
Staff manage each post's status and tags.

Files:
    templates.py  template model and validation
    storage.py    this feature's tables
    context.py    shared state passed to commands and views
    forms.py      pinned Start here post and the form pages
    posting.py    rendering and publishing posts
    tags.py       forum tag helpers
    admin.py      /forums setup, remove, list (a channel can offer several forms)
    staff.py      Manage post tool
    status.py     changing a post's status, and the poster's own close button
    ratings.py    member ratings with a rubric, and /leaderboard
    rating_moderation.py  staff tools: Remove rating, /ratings block, unblock, blocked
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .admin import ForumAdmin
from .context import ForumsContext
from .forms import FormPanel
from .rating_moderation import RatingModeration
from .ratings import Leaderboard, RatingView
from .staff import ForumStaff
from .status import AuthorCloseView
from .storage import ForumStorage
from .templates import load_templates

if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule


async def setup(bot: MarketMule) -> None:
    templates = load_templates(bot.config.templates_dir / "forums")
    storage = ForumStorage(bot.db)
    await storage.migrate()

    forums = ForumsContext(bot=bot, storage=storage, templates=templates)
    # Keeps the buttons on existing pinned posts working after restarts.
    bot.add_view(FormPanel(forums, list(templates.values())))
    for template in templates.values():
        if template.author_close is not None:
            bot.add_view(AuthorCloseView(forums, template))
        if template.rating is not None:
            bot.add_view(RatingView(forums, template))
    await bot.add_cog(ForumAdmin(forums))
    await bot.add_cog(ForumStaff(forums))
    await bot.add_cog(Leaderboard(forums))
    await bot.add_cog(RatingModeration(forums))
