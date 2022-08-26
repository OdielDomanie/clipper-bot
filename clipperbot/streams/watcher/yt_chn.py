import asyncio as aio
import logging
import re
from typing import TYPE_CHECKING

from ...vtuber_names import channels_list
from ..stream import all_streams
from ..stream.base import Stream, StreamStatus
from ..stream.yt import YTStream, yt_stream_uid
from ..yt_dlp_extractor import fetch_yt_metadata
from .base import Poller


logger = logging.getLogger(__name__)


class YtChnWatcher(Poller):

    @classmethod
    def url_is_valid(cls, url: str) -> bool:
        return bool(re.match(
            r"^https:\/\/www\.youtube\.com\/channel\/([a-zA-Z0-9\-_]{24})$",
            url
        ))

    def __init__(self, target: str):
        self.targets_url = target
        chn_id = target[-24:]
        try:
            _, self.name, self.en_name = channels_list[chn_id]
        except KeyError:
            self.name = target.split("/")[-1]
            self.en_name = None
        super().__init__(target)

    async def _poll(self) -> None | Stream:

        metadata_dict = await aio.to_thread(fetch_yt_metadata, self.target + "/live")

        if not metadata_dict:
            logger.log(
                logging.DEBUG,
                f"No metadata received from {self.target};"
                f" {self.target} is not live."
            )
            return None

        if not metadata_dict.get("is_live"):
            logger.log(logging.DEBUG, f"{self.target} is not live.")
            return None

        stream_url = metadata_dict["webpage_url"]
        # title includes fetch date for some reason
        stream_title = metadata_dict["title"][:-17]

        uid = yt_stream_uid(stream_url)
        if uid in all_streams:
            stream = all_streams[uid]
            stream.online = StreamStatus.ONLINE
        else:
            stream = YTStream(
                stream_url, stream_title, StreamStatus.ONLINE, metadata_dict
            )
            all_streams[uid] = stream

        logger.log(
            logging.DEBUG,
            f"{self.target} is live with at {stream_url} / {stream_title}"
        )

        return stream

    def is_alias(self, name: str) -> bool:
        return (
            name in self.target
            or name in (self.name or "")
            or (self.en_name is not None and name in self.en_name)
        )


if TYPE_CHECKING:
    YtChnWatcher("")
