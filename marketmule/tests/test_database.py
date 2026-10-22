import asyncio
import sqlite3
from pathlib import Path

import pytest

from marketmule.core.database import Database
from marketmule.core.staff import get_staff_role, set_staff_role

# Plain CREATE TABLE (no IF NOT EXISTS) fails if run twice, so these also prove nothing re-runs.
FIRST = "CREATE TABLE widgets (id INTEGER PRIMARY KEY);"
SECOND = "ALTER TABLE widgets ADD COLUMN name TEXT;"


def run(coro):
    return asyncio.run(coro)


async def open_db(path: Path) -> Database:
    db = Database(path)
    await db.connect()
    return db


def test_migrations_run_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = await open_db(tmp_path / "test.db")
        await db.migrate("widgets", [FIRST])
        await db.migrate("widgets", [FIRST])  # second call is a no-op
        assert await db.schema_version("widgets") == 1
        await db.close()

    run(scenario())


def test_new_migrations_apply_to_existing_database(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "test.db"
        db = await open_db(path)
        await db.migrate("widgets", [FIRST])
        await db.close()

        # Simulates deploying a code update that appends a migration.
        db = await open_db(path)
        await db.migrate("widgets", [FIRST, SECOND])
        await db.execute("INSERT INTO widgets (id, name) VALUES (1, 'ok')")
        assert await db.schema_version("widgets") == 2
        await db.close()

    run(scenario())


def test_failed_migration_leaves_database_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = await open_db(tmp_path / "test.db")
        broken = "CREATE TABLE half_done (id INTEGER); THIS IS NOT SQL;"
        with pytest.raises(sqlite3.OperationalError):
            await db.migrate("widgets", [broken])
        assert await db.schema_version("widgets") == 0
        assert await db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'half_done'") is None
        await db.close()

    run(scenario())


def test_refuses_database_newer_than_code(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = await open_db(tmp_path / "test.db")
        await db.migrate("widgets", [FIRST, SECOND])
        with pytest.raises(RuntimeError, match="only knows 1"):
            await db.migrate("widgets", [FIRST])
        await db.close()

    run(scenario())


def test_features_are_versioned_separately(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = await open_db(tmp_path / "test.db")
        await db.migrate("widgets", [FIRST])
        await db.migrate("gadgets", ["CREATE TABLE gadgets (id INTEGER);"])
        assert await db.schema_version("widgets") == 1
        assert await db.schema_version("gadgets") == 1
        await db.close()

    run(scenario())


def test_staff_role_setting(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = await open_db(tmp_path / "test.db")
        assert await get_staff_role(db, 1) is None
        await set_staff_role(db, 1, 1234)
        assert await get_staff_role(db, 1) == 1234
        assert await get_staff_role(db, 2) is None
        await db.close()

    run(scenario())
