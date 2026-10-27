"""Staff shortcuts: one-word slash commands that set a post's status and post a ready-made message.

Each shortcut is set up in a template, for example /nab on bug reports:

    "staff_shortcuts": [
      {"command": "nab",
       "description": "Close as Not A Bug and send the poster to support",
       "status": "Not A Bug",
       "message": "Hi {user}, ... please open a ticket here: https://csfloat.com/support",
       "button_label": "Open a support ticket",
       "button_url": "https://csfloat.com/support"}
    ]

{user} in the message becomes a ping of the person who posted. The button is optional.
Several templates can define the same command; inside a post, it uses that post's template.
Commands are built when the bot starts, so a new or renamed shortcut needs a restart.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui

from marketmule.core.staff import is_staff

from .status import set_status
from .templates import StaffShortcut

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)


def build_shortcut_commands(forums: ForumsContext) -> list[app_commands.Command]:
    """One slash command per shortcut name used in any template."""
    descriptions: dict[str, str] = {}
    for template in forums.templates.values():
        for shortcut in template.staff_shortcuts:
            descriptions.setdefault(shortcut.command, shortcut.description)
    return [_make_command(forums, name, description) for name, description in sorted(descriptions.items())]


def _make_command(forums: ForumsContext, name: str, description: str) -> app_commands.Command:
    @app_commands.default_permissions(manage_threads=True)
    @app_commands.guild_only()
    async def callback(interaction: discord.Interaction) -> None:
        await run_shortcut(forums, interaction, name)

    return app_commands.Command(name=name, description=description, callback=callback)


def support_button(shortcut: StaffShortcut) -> ui.View | None:
    if shortcut.button_label is None or shortcut.button_url is None:
        return None
    view = ui.View(timeout=None)
    view.add_item(ui.Button(label=shortcut.button_label, url=shortcut.button_url))
    return view


async def run_shortcut(forums: ForumsContext, interaction: discord.Interaction, name: str) -> None:
    if not isinstance(interaction.user, discord.Member) or not await is_staff(forums.bot.db, interaction.user):
        await interaction.response.send_message(f"Only staff can use /{name}.", ephemeral=True)
        return

    post = await forums.storage.get_post(interaction.channel_id or 0)
    template = forums.templates.get(post.template_id) if post else None
    if post is None or template is None:
        await interaction.response.send_message(f"Use /{name} inside a post created by MarketMule.", ephemeral=True)
        return
    shortcut = template.shortcut(name)
    if shortcut is None:
        await interaction.response.send_message(
            f"/{name} isn't available for {template.name.lower()} posts.", ephemeral=True
        )
        return
    if post.status == shortcut.status:
        await interaction.response.send_message(f"This post is already marked **{post.status}**.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    message = shortcut.message.replace("{user}", f"<@{post.reporter_id}>")
    result = await set_status(
        forums, post, template, interaction.user, shortcut.status, announcement=message, view=support_button(shortcut)
    )
    log.info("%s used /%s in %s", interaction.user, name, post.thread_id)
    await interaction.followup.send(result, ephemeral=True)
