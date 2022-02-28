import logging
from . import ClipBot

from .. import LOG_FILE, LOG_LVL, DEFAULT_PREFIX, DATABASE, TOKEN, OWNER_ID


handler = logging.FileHandler(LOG_FILE)
handler.setFormatter(
    logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
)

logger_asyncio = logging.getLogger("asyncio")
logger_asyncio.setLevel(logging.WARNING)
logger_asyncio.addHandler(handler)

logger_clipper = logging.getLogger("clipping")
logger_clipper.setLevel(LOG_LVL)
logger_clipper.addHandler(handler)

logger_clipper = logging.getLogger("discord")
logger_clipper.setLevel(LOG_LVL)
logger_clipper.addHandler(handler)


def run():
    bot = ClipBot(
        DEFAULT_PREFIX,
        database=DATABASE,
        owner_id=OWNER_ID
    )
    return bot.run(TOKEN)
