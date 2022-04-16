import asyncio as aio
import http
import http.cookiejar
import logging
import random
import time
from datetime import datetime, timedelta

import youtube_dl as ytdl

from .exceptions import RateLimited

logger = logging.getLogger("clipping.listen")


# @functools.lru_cache(maxsize=1000)
async def fetch_yt_metadata(chn_url):
    """Fetches metadata of url, with `noplaylist`.
    Returns `info_dict`. Can raise `RateLimited`.
    """
    info_dict = await aio.to_thread(_fetch_yt_metadata, chn_url)
    return info_dict


ytdl_logger = logging.getLogger("ytdl_fetchinfo")
ytdl_logger.addHandler(logging.NullHandler())  # f yt-dl logs
repeated_errors: dict[
    str, list
] = {}  # {url: [last_dtime, {err1, err2,}]}. Memory leak here!


def _fetch_yt_metadata(url: str):
    """Fetches metadata of url, with `noplaylist`.
    Returns `info_dict`. Can raise `RateLimited`.
    """

    # options referenced from
    # https://github.com/sparanoid/live-dl/blob/3e76be969d94747aa5d9f83b34bc22e14e0929be/live-dl
    #
    # Problem with cookies in current implementation:
    # https://github.com/ytdl-org/youtube-dl/issues/22613
    ydl_opts = {
        "logger": ytdl_logger,
        "noplaylist": True,
        "playlist_items": "0",
        "skip_download": True,
        "forcejson": True,
        "no_color": True,
        "cookiefile": "cookies.txt",
    }

    if "youtube.com/" in url or "youtu.be/":
        ydl_opts["referer"] = "https://www.youtube.com/feed/subscriptions"

    try:
        with ytdl.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
    except ytdl.utils.DownloadError as e:
        # "<channel_name> is offline error is possible in twitch
        if "This live event will begin in" in e.args[0] or "is offline" in e.args[0]:
            logger.debug(e)
        elif "HTTP Error 429" in e.args[0]:
            logger.critical(f'Got "{e}", for {url}.')
            raise RateLimited(url, logger=logger)
        else:
            if url not in repeated_errors:
                repeated_errors[url] = [datetime.now(), set()]
            REPEAT_LIMIT = timedelta(minutes=30)
            if (
                e.args[0] not in repeated_errors[url][1]
                and datetime.now() - repeated_errors[url][0] < REPEAT_LIMIT
            ):
                logger.error(f"{e}, for {url}.")
                repeated_errors[url][1].add(e.args[0])
                repeated_errors[url][0] = datetime.now()
        return None
    except http.cookiejar.LoadError as e:
        logger.error(f"Cookie error: {e}. Trying again")
        time.sleep(random.random() * 3)
        return fetch_yt_metadata(url)
    except Exception as e:
        logger.exception(e)
        return None
    return info_dict
