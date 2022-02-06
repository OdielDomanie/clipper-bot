import asyncio
import logging
import datetime as dt
import os
from discord import TextChannel
from ..video.download import (StreamDownload, 
    wait_for_stream, RateLimited)
from .. import utils
from ..utils import clean_space

from .. import POLL_INTERVAL


logger = logging.getLogger("clipping.botstreams")


async def listen(bot, txtchn:TextChannel, chn_url):
    """Starts a stream download when channel goes live.
    Handles sharing the download and cleanup.
    """
    global ratelimit
    logger.info(f"Listening to {chn_url} on {txtchn}.")
    while True:
        try:
            vid_url, title, start_time = await wait_for_stream(chn_url)
        except ValueError as e:
            logger.error(str(e))
            return e
        except Exception as e:
            logger.exception(e)
            return e
        
        try:
            await create_stream(bot, txtchn, vid_url, title, start_time=start_time)
        except RateLimited:          
                logger.critical(f"Waiting {2 ** ratelimit} minutes).")
                await asyncio.sleep(2 ** ratelimit * 60)
                ratelimit += 1
        except Exception:
            await asyncio.sleep(POLL_INTERVAL)
        
        logger.info("Continuing listening.")


ratelimit = 0
async def create_stream(bot, txtchn, vid_url, title, start_time):
    """Creates and starts (if necessary) 
    and registers a stream to a text channel,
    cleans up and returns when the stream ends."""
    global ratelimit
    try:
        stream = None
        for other_txtchn, existing_stream in bot.streams.items():
            if existing_stream.vid_url == vid_url and not existing_stream.done:
                stream = existing_stream
                break
        if not stream:
            stream = StreamDownload(vid_url, title, start_time=start_time)
            try:
                os.remove(bot.streams[txtchn.id].filepath)
            except (FileNotFoundError, KeyError):
                pass
        bot.streams[txtchn] = stream
        bot.active_files.append(stream.filepath)
        try:
            await stream_started_msg(txtchn, title, vid_url)
            await stream.start_download()
        except RateLimited:
            logger.critical("Download ratelimited.")
            raise
        else:
            ratelimit -= 1  # arbitrary, need more info on yt rate limit behavior.
            ratelimit = max(ratelimit, 0)
    except Exception as e:
        logger.exception(e)
        try: await stream_stopped_msg(txtchn, title, vid_url, e)
        except Exception: pass
        raise
    else:
        try: await stream_stopped_msg(txtchn, title, vid_url)
        except Exception: pass
    finally:

        try: bot.active_files.remove(stream.filepath)
        except Exception: pass


# Prevent spamming in the case of a bug, as these messages can be sent
# without user prompt.
# The constants should be replaced by configs.
RT_TIME = dt.timedelta(hours=8)
RT_REQS = 5
auto_msg_ratelimits = {}  # {channel_id: RateLimit}
async def stream_started_msg(txtchn:TextChannel, title, vid_url):
    logger.info(f"Stream {title} ({vid_url}) started at"
        f" {txtchn.guild.name}/{txtchn.name}.")
    try:
        rate_limit = auto_msg_ratelimits.setdefault(txtchn.id, utils.RateLimit(RT_TIME, RT_REQS))
        skipping_msg = rate_limit.skip(txtchn.send)
        await skipping_msg(f"Capturing stream: {title} (<{vid_url}>)")
        # await auto_msg_ratelimits.setdefault(
        #     txtchn.id, utils.RateLimit(RT_TIME, RT_REQS)
        # ).skip(txtchn.send
        # )(
        #     f"Capturing stream: {title} (<{vid_url}>)"
        # )
    except Exception as e:
        logger.error(f"Can't send \"stream started\" message: {e}")


async def stream_stopped_msg(txtchn:TextChannel, title, vid_url, exception=None):
    logger.info(f"Stream {title} ({vid_url}) stopped at"
        f" {txtchn.guild.name}/{txtchn.name}.")
    # try:
    #     if isinstance(exception, RateLimited):
    #         await auto_msg_ratelimits.setdefault(
    #             txtchn.id, utils.RateLimit(RT_TIME, RT_REQS)
    #         ).skip(txtchn.send
    #         )(
    #             f"Stopped Capturing stream: {title} (<{vid_url}>)."
    #             "\nBot is rate limited :("
    #         )
    #     else:
    #         await auto_msg_ratelimits.setdefault(
    #             txtchn.id, utils.RateLimit(RT_TIME, RT_REQS)
    #         ).skip(txtchn.send
    #         )(
    #             f"Stopped Capturing stream: {title} (<{vid_url}>)."
    #         )
    # except Exception as e:
    #     logger.error(f"Can't send \"stream stopped\" message: {e}")


async def periodic_cleaning(dir, max_size, no_delete, frequency=1800):
    "`no_delete` can be modified externally while this is running."
    while True:
        clean_space(dir, max_size, no_delete)
        await asyncio.sleep(frequency)
