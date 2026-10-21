"""Entry point: python -m marketmule"""

import logging
import sys

from discord.ext import commands

from marketmule.core.bot import MarketMule
from marketmule.core.config import ConfigError, load_config

log = logging.getLogger("marketmule")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ConfigError as exc:
        log.error("%s", exc)
        sys.exit(1)

    bot = MarketMule(config)
    try:
        bot.run(config.token, log_handler=None)
    except commands.ExtensionFailed as exc:
        # A feature failed to start, usually because of an invalid template.
        log.error("Could not load %s: %s", exc.name, exc.original)
        sys.exit(1)


if __name__ == "__main__":
    main()
