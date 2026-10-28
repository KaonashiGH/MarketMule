"""The MarketMule bot: shared services, feature loading and slash-command sync."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from marketmule.core.admin import CoreAdmin
from marketmule.core.config import Config
from marketmule.core.database import Database
from marketmule.core.icons import Icons
from marketmule.core.wordfilter import WordFilter

log = logging.getLogger(__name__)

# Features to load at startup. To add one, create marketmule/features/<name>/ and list it here.
FEATURES: tuple[str, ...] = ("marketmule.features.forums", "marketmule.features.stalls")


class CommandTree(app_commands.CommandTree):
    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("Command %s failed", interaction.command and interaction.command.qualified_name, exc_info=error)
        message = "Something went wrong running that command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def bot_intents() -> discord.Intents:
    """Default intents plus message text, which stalls need to check the links in members' posts.

    Message Content is a privileged intent: it must also be switched on in the Discord Developer Portal
    (your app → Bot → Message Content Intent), or Discord refuses the bot's connection."""
    intents = discord.Intents.default()
    intents.message_content = True
    return intents


class MarketMule(commands.Bot):
    def __init__(self, config: Config) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=bot_intents(),
            tree_cls=CommandTree,
        )
        self.config = config
        self.db = Database(config.database_path)
        self.word_filter = WordFilter(config.blocked_words_path)
        self.icons = Icons(config.icons_dir)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.icons.load(self)
        await self.add_cog(CoreAdmin(self))
        for feature in FEATURES:
            await self.load_extension(feature)
            log.info("Loaded feature %s", feature)
        await self._sync_commands()

    async def _sync_commands(self) -> None:
        if self.config.guild_id is None:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (may take up to an hour to appear)", len(synced))
            return

        guild = discord.Object(self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d commands to server %s", len(synced), self.config.guild_id)

        # Clear global copies so the server doesn't list every command twice.
        self.tree.clear_commands(guild=None)
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)

    async def close(self) -> None:
        await self.db.close()
        await super().close()
