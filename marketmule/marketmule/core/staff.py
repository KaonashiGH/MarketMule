"""Who counts as staff. Every feature uses the same check, so the staff role is set once per server."""

from __future__ import annotations

import discord

from marketmule.core.database import Database

STAFF_ROLE_KEY = "staff_role"


async def get_staff_role(db: Database, guild_id: int) -> int | None:
    value = await db.get_setting(guild_id, STAFF_ROLE_KEY)
    return int(value) if value else None


async def set_staff_role(db: Database, guild_id: int, role_id: int) -> None:
    await db.set_setting(guild_id, STAFF_ROLE_KEY, str(role_id))


async def is_staff(db: Database, user: discord.abc.User) -> bool:
    """Admins, members with Manage Threads, and members with the server's staff role."""
    if not isinstance(user, discord.Member):
        return False
    permissions = user.guild_permissions
    if permissions.administrator or permissions.manage_threads:
        return True
    role_id = await get_staff_role(db, user.guild.id)
    return role_id is not None and user.get_role(role_id) is not None
