import asyncio as aio
import logging
import re
from typing import TYPE_CHECKING

from ..stream import all_streams
from ..stream.base import Stream, StreamStatus, StreamWithActDL
from ..stream.yt import YTStream, yt_stream_uid
from ..yt_dlp_extractor import fetch_yt_metadata
from .base import Poller


logger = logging.getLogger(__name__)


class YtStrmWatcher(Poller):

    @classmethod
    def url_is_valid(cls, url: str) -> bool:
        return bool(re.match(
            r"^https:\/\/www\.youtube\.com\/watch\?v=[a-zA-Z0-9\-_]{11}$",
            url
        ))

    def __init__(self, target: str):
        self.targets_url = target
        self.stream_title: str | None = None
        super().__init__(target)

    @property
    def name(self):
        return self.active_stream and self.active_stream.title

    def is_alias(self, name: str) -> bool:
        return (
            name in self.target
            or (bool(self.name) and name in self.name)
            or (self.stream_title is not None and name in self.stream_title)
        )


    async def _poll(self) -> None | Stream:

        metadata_dict = await aio.to_thread(fetch_yt_metadata, self.target)

        if not metadata_dict:
            logger.log(
                logging.DEBUG,
                f"No metadata received from {self.target};"
                f" {self.target} is not live."
            )
            return None

        stream_url = metadata_dict["webpage_url"]
        # title includes fetch date for some reason
        stream_title = metadata_dict["title"][:-17]
        self.stream_title = stream_title

        if not metadata_dict.get("is_live"):
            logger.log(logging.DEBUG, f"{self.target} is not live.")
            return None

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

    async def _watch(self):
        while True:
            try:
                s = await self._poll()
            except Exception as e:
                logger.exception(e)
                s = None
            if s:
                break
            else:
                await aio.sleep(self.poll_period)
        if s.online == StreamStatus.PAST:
            logger.info("Watched stream is already offline.")
            return
        logger.info(f"Stream started: {self.target}")
        for hs in self.start_hooks.values():
            for h in hs:
                await h(s)
        self.stream_off.clear()
        self.stream_on.set()
        assert isinstance(s, StreamWithActDL)
        self.active_stream = s
        while True:
            if not s.active:
                logger.info(f"Starting download for: {self.target}")
                s.start_download()
                await s.actdl_on.wait()
            await s.actdl_off.wait()
            logger.info(f"Stream download ended: {self.target}")
            # Did it really end?
            try:
                if not await self._poll():
                    logger.debug(f"Poll returned None after dl ended: {self.target}")
                    break
                else:
                    logger.warning(f"Stream dl ended but is still online: {self.target}")
                    await aio.sleep(self.poll_period/3)
            except Exception as e:
                logger.exception(e)
                break
        # Stream ended
        logger.info(f"Stream ended: {self.target}")
        s.online = StreamStatus.PAST
        self.stream_on.clear()
        self.stream_off.set()
        self.active_stream = None


if TYPE_CHECKING:
    YtStrmWatcher("")
