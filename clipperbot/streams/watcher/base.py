import asyncio as aio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Awaitable, Callable

from ... import POLL_PERIOD
from ..exceptions import DownloadForbidden
from ..stream.base import Stream, StreamStatus, StreamWithActDL

if TYPE_CHECKING:
    from .share import WatcherSharer


logger = logging.getLogger(__name__)


class Watcher(ABC):

    @classmethod
    @abstractmethod
    def url_is_valid(cls, url: str) -> bool:
        ...

    def __init__(self, target: str):
        ...

    start_hooks: dict["WatcherSharer", tuple[Callable[[Stream], Awaitable], ...]]
    targets_url: str
    name: str
    active_stream: Stream | None
    stream_on: aio.Event
    stream_off: aio.Event

    @abstractmethod
    def start(self):
        "Start watching. Will also start the active download of the stream."

    @abstractmethod
    def stop(self):
        "Stop watching. Will also stop the active download."

    @abstractmethod
    def is_alias(self, name: str) -> bool:
        "Can be a partial alias."


class Poller(Watcher):

    poll_period = POLL_PERIOD

    def __init__(self, target: str):
        self.target = target
        self.active_stream = None
        self.stream_on = aio.Event()
        self.stream_off = aio.Event()
        self.stream_off.set()
        self._watch_task: aio.Task | None = None
        self.start_hooks = {}

    @abstractmethod
    async def _poll(self) -> None | Stream:
        "Poll, and return the stream if live."

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
                logger.info(f"Stream started: {self.target}")
                for hs in list(self.start_hooks.values()):  # start_hook might be appended in another task
                    for h in hs:
                        try:
                            await h(s)
                        except Exception as e:
                            logger.exception(e)
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
            await aio.sleep(self.poll_period)


    def start(self):
        assert not self._watch_task
        self._watch_task = aio.create_task(self._watch())

    def stop(self):
        assert self._watch_task
        self._watch_task.cancel()
