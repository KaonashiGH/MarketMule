"""Bot features. Each subpackage is one self-contained feature.

To add a feature:
    1. Create features/<name>/ with an __init__.py that defines `async def setup(bot)`.
       That function loads the feature's data, runs its migrations and adds its cogs.
    2. Keep the feature's tables in its own storage.py with a MIGRATIONS tuple,
       and call `bot.db.migrate("<name>", MIGRATIONS)` from setup.
    3. Put any templates in templates/<name>/.
    4. Add "marketmule.features.<name>" to FEATURES in core/bot.py.

Features may import from marketmule.core, but never from each other.
"""
