import asyncio as aio
import logging
import time
from typing import TYPE_CHECKING, Any, Collection, Optional

import discord as dc
import discord.app_commands as ac
import discord.ext.commands as cm

from .. import DATABASE
from ..persistent_dict import OldPersistentDict, PersistentDict, PersistentSetDict
from ..streams.stream import all_streams, clean_space
from ..streams.stream.get_stream import get_stream
from ..streams.url_finder import get_channel_url, san_stream_or_chn_url
from ..streams.watcher.share import WatcherSharer, create_watch_sharer
from ..utils import RateLimit, thinking
from ..vtuber_names import get_all_chns_from_name, get_from_chn
from . import help_strings
from .exceptions import StreamNotLegal

if TYPE_CHECKING:
    from discord.abc import PartialMessageableChannel
    from ..streams.stream.base import Stream
    from .bot import ClipperBot


logger = logging.getLogger(__name__)


def send_news(chn: "PartialMessageableChannel", guild_id: int, bot: "ClipperBot"):
    if bot.upd_news_unsent.get(guild_id):
        aio.create_task(chn.send(
"""**Version 2.0 Update**
**New stuff:**
* Optional Slash Commands
* Clip past VODs
* `register` Twitch channels
* Interactive editing with `/edit`
* No need to add the dash (`-`) at the end anymore

View the updated usage guide and all of the new features at <https://odieldomanie.github.io/callipper/>
"""
        ))
        try:
            chn_name = chn.name  # type: ignore
        except AttributeError:
            chn_name = None
        logger.info(f"Posting update news at {guild_id, chn.id, chn_name}")
        bot.upd_news_unsent[guild_id] = False
