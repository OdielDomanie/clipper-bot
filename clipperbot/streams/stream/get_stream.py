import asyncio as aio
import logging

from ..url_finder import get_stream_url
from ..yt_dlp_extractor import fetch_yt_metadata
from . import all_streams
from .base import Stream, StreamStatus
from .ttv import TTVStream
from .yt import YTStream


logger = logging.getLogger(__name__)


async def get_stream(stream_name: str) -> Stream | None:

    for s in all_streams.values():
        if s.is_alias(stream_name):
            return s

    try:
        url, info_dict = await get_stream_url(stream_name)
    except ValueError:
        return None
    else:
        if not info_dict:
            info_dict = await aio.to_thread(fetch_yt_metadata, url)
            assert info_dict
        if YTStream.url_is_valid(url):
            title = info_dict["title"]
            if info_dict.get("is_live"):
                online = StreamStatus.ONLINE
            elif info_dict.get("live_status") == "is_upcoming":
                # online = StreamStatus.FUTURE  # Let's don't deal with future streams
                return None
            elif info_dict.get("was_live"):
                online = StreamStatus.PAST
            elif "live_chat" in info_dict.get("subtitles", {}):  # past premiere
                online = StreamStatus.PAST
            else:
                return None
            s = YTStream(url, title, online, info_dict)

        elif TTVStream.url_is_valid(url):
            title = info_dict["description"]
            if info_dict.get("is_live"):
                online = StreamStatus.ONLINE
            elif info_dict.get("was_live"):
                online = StreamStatus.PAST
            else:
                return None
            s = TTVStream(url, title, online, info_dict)
        else:
            logger.error(f"Url not supported: {url}")
            return None

    if s.unique_id in all_streams:
        logger.error(f"Stream {s} was not found in all_streams, but its uid is present.")
        s = all_streams[s.unique_id]
        s.info_dict = info_dict
        s.online = online
    else:
        all_streams[s.unique_id] = s
    return s
