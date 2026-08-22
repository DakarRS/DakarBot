from __future__ import annotations

import logging

from dakarbot import settings
from dakarbot.bot import DakarBot


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = DakarBot()
    bot.run(settings.TOKEN)


if __name__ == "__main__":
    main()
