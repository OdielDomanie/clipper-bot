import logging
from .bot import ClipperBot
import discord as dc
import os
import dotenv

from .. import LOG_LVL, DEFAULT_PREFIX, DATABASE


handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
)

logger_asyncio = logging.getLogger("asyncio")
logger_asyncio.setLevel(logging.INFO)
logger_asyncio.addHandler(handler)

logger_clipper = logging.getLogger("clipperbot")
logger_clipper.setLevel(LOG_LVL)
logger_clipper.addHandler(handler)

logger_clipper = logging.getLogger("discord")
logger_clipper.setLevel(logging.INFO)
logger_clipper.addHandler(handler)


dotenv.load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
assert DISCORD_TOKEN

intents = dc.Intents(
            guilds=True,
            guild_messages=True,
            guild_reactions=True
        )

def run():
    bot = ClipperBot(
        DEFAULT_PREFIX,
        database=DATABASE,
        intents=intents,
    )
    return bot.run(DISCORD_TOKEN, log_handler=None)
