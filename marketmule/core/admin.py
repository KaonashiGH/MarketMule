"""Server-wide settings commands: /marketmule staffrole, /marketmule icons, /marketmule status."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from marketmule.core.staff import get_staff_role, set_staff_role

if TYPE_CHECKING:
    from marketmule.core.bot import MarketMule


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class CoreAdmin(commands.GroupCog, group_name="marketmule", group_description="MarketMule server settings"):
    def __init__(self, bot: MarketMule) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name="staffrole", description="Choose which role counts as staff in every feature")
    @app_commands.describe(role="Members with this role can use staff tools")
    async def staff_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            return
        await set_staff_role(self.bot.db, interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"{role.mention} is now the staff role. Admins and members with Manage Threads always count as staff.\n"
            "To show staff tools to this role, add it under Server Settings → Integrations → MarketMule.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="icons", description="Upload MarketMule's icons as the bot's emojis")
    async def icons(self, interaction: discord.Interaction) -> None:
        if not self.bot.icons.files():
            await interaction.response.send_message("No icons found in `assets/icons/`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        uploaded, failed = await self.bot.icons.upload_missing(self.bot)
        lines = [
            f"Uploaded **{len(uploaded)}** new icons. The bot now has **{len(self.bot.icons)}**.",
            "New posts use them right away. Run `/forums setup` again to refresh Start here posts.",
        ]
        if failed:
            lines.append("Couldn't upload: " + ", ".join(failed))
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="status", description="Show the bot's loaded features and settings")
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        role_id = await get_staff_role(self.bot.db, interaction.guild.id)
        features = ", ".join(name.rsplit(".", 1)[-1] for name in self.bot.extensions) or "none"
        await interaction.response.send_message(
            f"**Features:** {features}\n**Staff role:** {f'<@&{role_id}>' if role_id else 'not set'}\n"
            f"**Icons:** {len(self.bot.icons)} uploaded",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
