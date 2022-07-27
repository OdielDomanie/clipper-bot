import http.cookiejar
import logging
import time

import yt_dlp

from .exceptions import RateLimited


logger = logging.getLogger(__name__)


# {url: [last_dtime, {err1, err2,}]}
repeated_errors: dict[str, list] = {}
def fetch_yt_metadata(url: str, *, no_playlist=True, playlist_items=None):
    """Fetches metadata of url, with `noplaylist`.
    Returns `info_dict`. Can raise `RateLimited`.
    """

    ytdl_logger = logging.getLogger("ytdl_fetchinfo")
    ytdl_logger.addHandler(logging.NullHandler())  # f yt-dl logs

    # Problem with cookies in current implementation:
    # https://github.com/ytdl-org/youtube-dl/issues/22613
    ydl_opts = {
        "logger": ytdl_logger,
        "noplaylist": no_playlist,
        "playlist_items": playlist_items or "0",
        "skip_download": True,
        "forcejson": True,
        "no_color": True,
        "cookiefile": "cookies.txt",
        "ignore_no_formats_error": True,  # no error on upcoming
    }

    if "youtube.com/" in url:
        ydl_opts["referer"] = "https://www.youtube.com/feed/subscriptions"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        # "<channel_name> is offline error is possible in twitch
        if (
            "This live event will begin in" in e.args[0]
            or "is offline" in e.args[0]
        ):
            logger.debug(e)
        elif "HTTP Error 429" in e.args[0]:
            logger.critical(f"Got \"{e}\", for {url}.")
            raise RateLimited
        else:
            if url not in repeated_errors:
                repeated_errors[url] = [time.monotonic(), set()]
            REPEAT_LIMIT = 30 * 60
            if (
                e.args[0] not in repeated_errors[url][1]
                and time.monotonic() - repeated_errors[url][0] < REPEAT_LIMIT
            ):
                logger.error(f"{e}, for {url}.")
                repeated_errors[url][1].add(e.args[0])
                repeated_errors[url][0] = time.monotonic()
        return None
    except http.cookiejar.LoadError as e:
        logger.error(f"Cookie error: {e}. Trying again")
        time.sleep(1)
        return fetch_yt_metadata(url)
    except Exception as e:
        logger.exception(e)
        return None
    return info_dict
