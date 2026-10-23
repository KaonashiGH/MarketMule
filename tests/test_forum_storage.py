import asyncio
from pathlib import Path

from marketmule.core.database import Database
from marketmule.features.forums.storage import MIGRATIONS, Binding, ForumStorage, Post


def run(coro):
    return asyncio.run(coro)


def test_upgrade_keeps_existing_channels(tmp_path: Path) -> None:
    """A database from before multi-form channels keeps its channel and form after upgrading."""

    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        await db.migrate("forums", MIGRATIONS[:1])  # the old, one-form-per-channel schema
        await db.execute(
            "INSERT INTO forum_bindings (channel_id, guild_id, template_id, webhook_url, panel_thread_id)"
            " VALUES (10, 1, 'bug_report', 'https://hook', 99)"
        )

        storage = ForumStorage(db)
        await storage.migrate()

        assert await storage.get_binding(10) == Binding(
            channel_id=10, guild_id=1, template_ids=("bug_report",), webhook_url="https://hook", panel_thread_id=99
        )
        await db.close()

    run(scenario())


def test_channel_with_two_forms(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        storage = ForumStorage(db)
        await storage.migrate()

        binding = Binding(
            channel_id=10, guild_id=1, template_ids=("loadout", "tradeup"), webhook_url=None, panel_thread_id=None
        )
        await storage.save_binding(binding)
        assert await storage.get_binding(10) == binding
        assert await storage.list_bindings(1) == [binding]

        # Removing a form keeps the others, in order.
        await storage.save_binding(Binding(10, 1, ("tradeup",), None, None))
        assert (await storage.get_binding(10)).template_ids == ("tradeup",)

        await storage.delete_binding(10)
        assert await storage.get_binding(10) is None
        await db.close()

    run(scenario())


def test_each_form_numbers_its_own_posts(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        storage = ForumStorage(db)
        await storage.migrate()

        await storage.add_post(Post(1, 10, "loadout", 1, 5, "Posted"))
        await storage.add_post(Post(2, 10, "loadout", 2, 5, "Posted"))
        assert await storage.next_post_number(10, "loadout") == 3
        assert await storage.next_post_number(10, "tradeup") == 1
        await db.close()

    run(scenario())
