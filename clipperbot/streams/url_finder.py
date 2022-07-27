import asyncio as aio
import logging
import re
from typing import Collection

from ..vtuber_names import get_chns_from_name
from .yt_dlp_extractor import fetch_yt_metadata


logger = logging.getLogger(__name__)


async def get_channel_url(chn_name: str) -> Collection[str]:
    "Get a list of sanitized urls."

    result = list[str]()
    if "." in chn_name and "/" in chn_name:  # a url

        if chn_id := re.search(  # youtube url
            r"(?<=youtube\.com\/channel\/)([a-zA-Z0-9\-_]{24})(?![a-zA-Z0-9\-_])",
            chn_name,
        ):
            return ("https://www.youtube.com/channel/" + chn_id.group(),)

        elif streamer_name := re.search(  # twitch
            r"(?<=twitch\.tv\/)([a-zA-Z0-9\-_]+?)(?![a-zA-Z0-9\-_])",
            chn_name,
        ):
            return ("https://www.twitch.tv/" + streamer_name.group(),)

        elif "youtube.com/watch" in chn_name or "youtu.be/" in chn_name:  # Stream urls are invalid.
            return []
        elif "twitch.tv/videos/" in chn_name:  # twitch_vod
            return []
        elif len(chn_name) == 24:  # chn id
            return ("https://www.youtube.com/channel/" + chn_name,)

        try:
            _, channel_urls, _, _ = get_chns_from_name(chn_name)
        except KeyError:
            pass
        else:
            return channel_urls

        # Unknown, try our best.  # TODO: Security vulnerability ???
        info_dict = await aio.to_thread(fetch_yt_metadata, chn_name)
        if info_dict and 'channel_url' in info_dict:
            logger.warning(
                f"Found data for unknown url: {chn_name}, {info_dict['channel_url']}"
            )
            return (info_dict['channel_url'],)

    return []


async def get_stream_url(stream_name: str) -> tuple[str, dict | None]:
    """Gets a stream url from stream link, channel link or channel name.
    Returns sanitized url and maybe an info_dict.
    Raises ValueError if not found.
    """
    # Is it a youtube channel url?
    if chn_id := re.search(
        r"(?<=youtube\.com\/channel\/)([a-zA-Z0-9\-_]{24})(?![a-zA-Z0-9\-_])",
        stream_name,
    ):
        stream_name = chn_id.group()  # set to channel id

    if "." in stream_name and "/" in stream_name:  # a url
        if url_id := re.search(
            r"(?<=youtube\.com/watch\?v=)([a-zA-Z0-9\-_]{11})(?![a-zA-Z0-9\-_])",
            stream_name,
        ):
            return "https://www.youtube.com/watch?v=" + url_id.group(), None
        elif url_id := re.search(
            r"(?<=youtu\.be\/)([a-zA-Z0-9\-_]{11})(?![a-zA-Z0-9\-_])",
            stream_name,
        ):
            return "https://www.youtube.com/watch?v=" + url_id.group(), None
        elif url_id := re.search(
            r"(?<=twitch\.tv\/videos\/)([0-9]+)(?![0-9])",
            stream_name,
        ):
            return "https://www.twitch.tv/videos/" + url_id.group(), None

        elif streamer_name := re.search(
                r"(?<=twitch\.tv\/)([a-zA-Z0-9\-_]+?)(?![a-zA-Z0-9\-_])",
                stream_name,
        ):
            return "https://www.twitch.tv/" + streamer_name.group(), None
        else:
            raise ValueError

    # either a youtube channel id, or youtube video id
    else:
        if len(stream_name) == 24:  # chn id
            # try to get stream if live
            base_url = "https://www.youtube.com/channel/" + stream_name
            info_dict = await aio.to_thread(fetch_yt_metadata, base_url + "/live")

            if info_dict and info_dict.get("is_live"):  # is live
                return "https://www.youtube.com/watch?v=" + info_dict["id"]

            else:  # is not live, get the last was_live vod
                info_dict_ls = await aio.to_thread(
                    fetch_yt_metadata,
                    base_url + "/videos?view=2&live_view=503",
                    no_playlist=False,
                    playlist_items=range(2),
                )
                info_dict_all = await aio.to_thread(
                    fetch_yt_metadata,
                    base_url + "/videos",
                    no_playlist=False,
                    playlist_items=range(2),
                )
                if info_dict_ls:
                    try:
                        ls_entry = info_dict_ls["entries"][0]
                    except (KeyError, IndexError):
                        ls_entry = None
                else:
                    ls_entry = None
                if info_dict_all:
                    try:
                        all_entry = info_dict_all["entries"][0]
                    except (KeyError, IndexError):
                        all_entry = None
                else:
                    all_entry = None

                if (
                    ls_entry
                    and all_entry
                    and (
                        all_entry.get("was_live")
                        or "live_chat" in all_entry.get("subtitles", [])
                    )
                ):
                    # Which is the earliest
                    last_live = (
                        ls_entry
                        if ls_entry["release_timestamp"]
                        > all_entry["release_timestamp"]
                        else all_entry
                    )
                else:
                    last_live = ls_entry or all_entry
                if not last_live:
                    raise ValueError
                return "https://www.youtube.com/watch?v=" + last_live["id"], last_live

        elif len(stream_name) == 11:  # video id
            return "https://www.youtube.com/watch?v=" + stream_name, None
        else:
            raise ValueError
