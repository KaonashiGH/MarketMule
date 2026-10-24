"""Settings loaded from the .env file in the project root."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int | None
    project_root: Path

    @property
    def database_path(self) -> Path:
        return self.project_root / "data" / "marketmule.db"

    @property
    def blocked_words_path(self) -> Path:
        return self.project_root / "config" / "blocked_words.txt"

    @property
    def icons_dir(self) -> Path:
        """PNG icons uploaded as the bot's app emojis by /marketmule icons."""
        return self.project_root / "assets" / "icons"

    @property
    def banners_dir(self) -> Path:
        """Banner images for Start here posts (and posts, if a form turns that on), e.g. pricecheck.png."""
        return self.project_root / "assets" / "banners"

    @property
    def templates_dir(self) -> Path:
        """Each feature keeps its templates in a subfolder, e.g. templates/forums/."""
        return self.project_root / "templates"


def load_config(project_root: Path = PROJECT_ROOT) -> Config:
    load_dotenv(project_root / ".env")

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")

    guild_id = os.getenv("GUILD_ID", "").strip()
    if guild_id and not guild_id.isdigit():
        raise ConfigError("GUILD_ID must be a server ID (numbers only).")

    return Config(
        token=token,
        guild_id=int(guild_id) if guild_id else None,
        project_root=project_root,
    )
