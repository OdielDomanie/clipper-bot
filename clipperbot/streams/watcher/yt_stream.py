import asyncio as aio
import logging
import re
from typing import TYPE_CHECKING

from ..exceptions import DownloadForbidden
from ..stream import all_streams, start_download, stop_download
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

        metadata_dict = await aio.to_thread(
            fetch_yt_metadata, self.target
        )
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

        if metadata_dict.get("was_live") or not metadata_dict.get("live_status"):
            logger.debug(f"{self.target} is past.")
            status = StreamStatus.PAST
        elif metadata_dict.get("is_live"):
            logger.debug(f"{self.target} is online.")
            status = StreamStatus.ONLINE
        else:
            logger.debug(f"{self.target} is not live.")
            return None

        if not metadata_dict.get("live_status"):
            logger.info(f"{self.target} is not a stream.")

        # TODO: Return stream that was in the past, or not a stream at all.
        uid = yt_stream_uid(stream_url)
        if uid in all_streams:
            stream = all_streams[uid]
            stream.online = status
            stream.info_dict = metadata_dict
        else:
            stream = YTStream(
                stream_url, stream_title, status, metadata_dict
            )
            all_streams[uid] = stream

        return stream

    async def _watch(self):
        while True:
            try:
                s = await self._poll()
            except DownloadForbidden as e:
                logger.info(e)
                return
            except Exception as e:
                logger.exception(e)
                s = None
            if s:
                break
            else:
                await aio.sleep(self.poll_period)
        logger.info(f"Stream found: {s.stream_url, s.title}")
        for hs in self.start_hooks.values():
            for h in hs:
                try:
                    await h(s)
                except Exception as e:
                    logger.exception(e)
        self.active_stream = s
        if s.online == StreamStatus.PAST:
            logger.info("Watched stream is already offline.")
            self.stream_on.set()
            self.stream_on.clear()
            self.stream_off.set()
            return
        logger.info(f"Stream started: {self.target}")
        self.stream_off.clear()
        self.stream_on.set()
        assert isinstance(s, StreamWithActDL)
        while True:
            logger.info(f"Starting download for: {self.target}")
            start_download(s)
            try:
                await s.actdl_on.wait()  # There is a race condition here but whatever
                await s.actdl_off.wait()
            finally:
                stop_download(s)
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
