"""Small Discord UI pieces shared by every feature."""

from __future__ import annotations

from collections.abc import Callable

import discord
from discord import ui

FIX_TIMEOUT = 15 * 60


def blocked_words_message(words: list[str]) -> str:
    listed = ", ".join(f"`{word}`" for word in words)
    return (
        f"Your answer contains words that aren't allowed here: {listed}\n"
        "Press **Edit my answers** to fix it. Everything else you typed is kept."
    )


class FixAnswersView(ui.View):
    """A button that reopens a form with the member's answers still filled in."""

    def __init__(self, reopen: Callable[[], ui.Modal]) -> None:
        super().__init__(timeout=FIX_TIMEOUT)
        self.reopen = reopen
        button: ui.Button = ui.Button(label="Edit my answers", emoji="✏️", style=discord.ButtonStyle.primary)
        button.callback = self.open
        self.add_item(button)

    async def open(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(self.reopen())
