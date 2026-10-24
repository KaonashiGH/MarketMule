"""MarketMule's own icons, stored as app emojis on the bot's Discord application.

The pictures live in assets/icons/ (one PNG per icon, named like mm_pricecheck.png). /marketmule icons
uploads any that are missing, so nobody needs the Developer Portal.

In any template text, write an icon as :mm_pricecheck:. The bot swaps it for the real emoji. If that icon
isn't uploaded yet, it's quietly left out, so a missing icon never breaks a post.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import discord

log = logging.getLogger(__name__)

# :mm_name:, but not the inside of an emoji that is already swapped in, like <:mm_name:123>.
TOKEN = re.compile(r"(?<![<\w]):(mm_[a-z0-9_]+):(?!\d)")
MAX_ICON_BYTES = 256 * 1024  # Discord's emoji size limit


class Icons:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._emojis: dict[str, discord.Emoji] = {}

    def __len__(self) -> int:
        return len(self._emojis)

    def set(self, emojis: list[discord.Emoji]) -> None:
        self._emojis = {e.name: e for e in emojis if e.name.startswith("mm_")}

    async def load(self, client: discord.Client) -> None:
        """Fetch the app's uploaded icons. If it fails, posts just go out without icons."""
        try:
            self.set(await client.fetch_application_emojis())
            log.info("Loaded %d icons", len(self._emojis))
        except discord.HTTPException:
            log.warning("Could not load icons; posts will go out without them")

    def text(self, name: str) -> str:
        """The emoji as message text, e.g. <:mm_pricecheck:123>. Empty if it isn't uploaded."""
        emoji = self._emojis.get(name.strip(":"))
        return str(emoji) if emoji else ""

    def emoji(self, name: str | None, fallback: str | None = None) -> discord.Emoji | str | None:
        """For buttons and menus: the uploaded icon, or the fallback (a normal emoji) if it's missing."""
        if name and (emoji := self._emojis.get(name.strip(":"))):
            return emoji
        return fallback

    def url(self, name: str | None) -> str | None:
        """Image link for the icon, for thumbnails."""
        emoji = self._emojis.get(name.strip(":")) if name else None
        return emoji.url if emoji else None

    def apply(self, text: str) -> str:
        """Swap every :mm_name: for its emoji, and tidy the gap a missing one leaves behind."""
        if ":mm_" not in text:
            return text
        swapped = TOKEN.sub(lambda m: self.text(m.group(1)), text)
        swapped = re.sub(r"[ \t]{2,}", " ", swapped)  # "## :mm_x: Title" → "## Title" when missing
        return re.sub(r"(?m)^ +| +$", "", swapped)

    def files(self) -> list[Path]:
        return sorted(p for p in self.folder.glob("mm_*.png") if p.is_file())

    async def upload_missing(self, client: discord.Client) -> tuple[list[str], list[str]]:
        """Upload icons from assets/icons/ that the app doesn't have yet. Returns (uploaded, failed)."""
        existing = {e.name for e in await client.fetch_application_emojis()}
        uploaded: list[str] = []
        failed: list[str] = []
        for path in self.files():
            name = path.stem
            if name in existing:
                continue
            data = path.read_bytes()
            if len(data) > MAX_ICON_BYTES or not re.fullmatch(r"[a-z0-9_]{2,32}", name):
                failed.append(f"{path.name} (over 256 KB or bad name)")
                continue
            try:
                await client.create_application_emoji(name=name, image=data)
                uploaded.append(name)
            except discord.HTTPException as exc:
                failed.append(f"{path.name} ({exc.text or exc.status})")
        await self.load(client)
        return uploaded, failed
