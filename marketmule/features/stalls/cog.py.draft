
from __future__ import annotations

import contextlib
import logging
from typing TYPE_CHECKING

import discord 
from discord import app_commands
from discord.ext import commands, tasks

from marketmule.core.discord_utils import fetch_thread 

from .import render, service
from .storage import StallChannel
from .views import StallPanel, close_stall



If TYPE_CHECKING:
    from .context import StallContex

log = logging.getLogger(__name__)

WEBHOOK_NAME = "Market stalls"
AUDIT_REASON = "MarkeyMule stall setup"
UPKEEP_HOURS = "2"

REQUIRED_PERMISSIONS = {
    "view_channel": "View Channel",
    "manage_channels": "Manage Channels",
    "manage_webhooks": "Manage Webhooks",
    "manage_threads": "Manage Threads",
    "send_message": "Create Posts",
    "send_messages_in_threads": "Send Messages in Posts",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
    
}

@app.commands.defult_permissions(manage_guild=True)
@app_commands.guild_only()
class Stalls(commands.GroupCog, group_name="Stalls", group_description="Member stalls"):
    def __init__(self,ctx: StallContex) -> None:
        self.ctx = ctx
        super().__init__()

    async def cog_load(self) -> None:
        self.upkeep.start()

    async def cog_unload(self) -> None:
        self.upkeep.start()


    @app_commands.command(name=" ", description="" )
    @app_commands.describe(channel="")
    async def setup_stalls(self, interation: discord.Interation, channel: discord.ForumChannel) -> None:
        guild = interaction.guild
        if guild is None:
            return
        missing = [ 
            label
            for attr, label in REQUIRED_PERMISSIONS.items()
            if not getattr(channel.permissions_for(guild.me),attr)
        ]
        if missing:
            await interation.response.send_message(
                f"The bot is missing these permission in {channel.mention}: {','.join(missing)}.", ephemeral=True
            )
            return 
        await interation.reponse.defer(ephemeral=True, thinking=True)
        
        tags = self.ctx.settings.tags.all()
        exsisting = 
        to_create = 
        if len(channel.available_tags)+ len(to_create) > 20:
                await interation.followup.send(f"{channel.mention} has too many tags. Remove some first", ephemerial=True) 
                return
        for name in to_create:
            emoji = self.ctx.setting.tag.emoji(name)
            await channel.create_tag(
                name=name       
                moderated=True,
                emoji=discord.PartiaEmoji(name=emoji) if emoji else None,
                reason=AUDIT_REASON,
            )
        
        
        
        
        
        
        for name in to_create:
            emoji = self.ctx.settings.tag_emojis.get(name)
            await channel.create_tag(
                name=name
                moderated=True,
                emoji=discord.partialemoji(name=emoji) if emoji else None,
            )

        )


#command setup



#keep stall open / listener



# math, trade hold ending





