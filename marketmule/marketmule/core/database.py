"""The shared SQLite database and per-feature schema migrations.

Every feature owns its own tables. It lists the SQL that builds them as a sequence of
migrations and passes them to `Database.migrate`, which runs only the ones a database
hasn't had yet. This is how existing databases, including the live one, get new tables
and columns when a feature changes.

Rules for migrations:
    - Never edit or reorder a migration once it has been deployed.
    - To change the schema, append a new migration (for example an ALTER TABLE).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

FEATURE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

CORE_MIGRATIONS: tuple[str, ...] = (
    # 1: per-server settings shared by all features, such as the staff role.
    """
    CREATE TABLE IF NOT EXISTS settings (
        guild_id INTEGER NOT NULL,
        key      TEXT    NOT NULL,
        value    TEXT,
        PRIMARY KEY (guild_id, key)
    );
    """,
)


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS schema_versions (feature TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        await self._db.commit()
        await self.migrate("core", CORE_MIGRATIONS)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected")
        return self._db

    # Migrations

    async def schema_version(self, feature: str) -> int:
        row = await self.fetch_one("SELECT version FROM schema_versions WHERE feature = ?", feature)
        return int(row[0]) if row else 0

    async def migrate(self, feature: str, migrations: Sequence[str]) -> None:
        """Bring a feature's tables up to date by running the migrations this database hasn't had."""
        if not FEATURE_NAME.match(feature):
            raise ValueError(f"Invalid feature name {feature!r}")

        current = await self.schema_version(feature)
        if current > len(migrations):
            raise RuntimeError(
                f"The database has {feature} schema version {current}, but this code only knows "
                f"{len(migrations)}. Update the code before running it against this database."
            )

        for version in range(current + 1, len(migrations) + 1):
            # One transaction per migration: either all of it applies, or none of it does.
            try:
                await self._conn.executescript(
                    "BEGIN;\n"
                    f"{migrations[version - 1]}\n"
                    f"INSERT OR REPLACE INTO schema_versions (feature, version) VALUES ('{feature}', {version});\n"
                    "COMMIT;"
                )
            except Exception:
                await self._conn.rollback()
                log.error("%s migration %d failed; the database was left unchanged", feature, version)
                raise
            log.info("Applied %s migration %d", feature, version)

    # Queries

    async def fetch_one(self, sql: str, *params: object) -> aiosqlite.Row | None:
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, *params: object) -> list[aiosqlite.Row]:
        async with self._conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def execute(self, sql: str, *params: object) -> None:
        await self._conn.execute(sql, params)
        await self._conn.commit()

    # Settings

    async def get_setting(self, guild_id: int, key: str) -> str | None:
        row = await self.fetch_one("SELECT value FROM settings WHERE guild_id = ? AND key = ?", guild_id, key)
        return row[0] if row else None

    async def set_setting(self, guild_id: int, key: str, value: str | None) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)", guild_id, key, value
        )
