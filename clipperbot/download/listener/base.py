import asyncio as aio
import logging
from abc import ABC, abstractmethod
from typing import Callable, Coroutine, Iterable, Optional

from ... import DOWNLOAD_DIR
from ..download import DownloadManager
from ..downloader import get_platform

logger = logging.getLogger("clipping.listen")


class Listener(ABC):
    "Starts a download when a channel goes live."

    DEF_POLL_INTERVAL = 60

    @abstractmethod
    def _get_poll_interval(self) -> float:
        ...

    @staticmethod
    @abstractmethod
    def general_validate(url) -> bool:
        "Preliminary check for url validity without sanitizing."
        ...

    @classmethod
    @abstractmethod
    async def sanitize_url(cls, url: str) -> str:
        """Sanitizes url to a standard. Raises `ValueError` if the url can't be used with this listener.
        Can raise `RateLimited`.
        """
        ...

    @staticmethod
    @abstractmethod
    def validate_url(url) -> bool:
        "Validate if the url is standard."
        ...

    @abstractmethod
    async def one_time(self) -> Optional[tuple[str, str, str, dict]]:
        """Return the stream url, stream title, output_fpath, and the info_dict if the channel is live, `None` otherwise.
        Can raise `RateLimited`.
        """
        ...

    def __init__(self, chn_url: str, download_dir=DOWNLOAD_DIR):
        assert self.validate_url(chn_url)
        self.chn_url: str = chn_url
        self._stream_title = None
        self.download_dir = download_dir
        self._is_live = aio.Event()
        self._listen_task: Optional[aio.Task] = None
        self._download: Optional[DownloadManager] = None

    @property
    def stream_title(self) -> str:
        assert self._stream_title
        return self._stream_title

    async def _listen_n_download(
        self,
        begin_hooks: Iterable[Callable[[], Coroutine]] = tuple(),
        end_hooks: Iterable[Callable[[], Coroutine]] = tuple(),
    ):
        """Start the download and call the hooks when the channel is live, continue listening when the download stops.
        Loop indefinitely.
        """
        while True:
            await aio.sleep(self._get_poll_interval())

            try:
                result = await self.one_time()
                if result:
                    stream_url, title, output_fpath, info_dict = result
                else:
                    continue
            except Exception as e:
                logger.exception(e)
                continue

            try:
                downloader, stream_url = get_platform(stream_url)
            except ValueError as e:
                logger.error(f"The stream url from listening is not supported. {e}")
                continue

            self._download = DownloadManager(
                stream_url, downloader, output_fpath, info_dict
            )
            self._download.start()
            self._is_live.set()
            self._stream_title = title

            for hook in begin_hooks:
                await hook()

            try:
                await self._download.wait_end()
            except Exception as e:
                logger.exception(e)
            finally:
                self._download.stop()
                self._is_live.clear()
                for hook in end_hooks:
                    await hook()

    def start(
        self,
        begin_hooks: Iterable[Callable[[], Coroutine]] = tuple(),
        end_hooks: Iterable[Callable[[], Coroutine]] = tuple(),
    ):
        "Start listening. When the channel is live, a download will be started."
        assert self._listen_task is None
        self._listen_task = aio.create_task(
            self._listen_n_download(begin_hooks, end_hooks)
        )

    def stop(self):
        "Stop listening. The download will be stopped."
        assert self._listen_task
        self._listen_task.cancel()
        self._listen_task = None

    async def wait_live(self):
        "Return when the channel is live."
        return await self._is_live.wait()

    @property
    def is_listening(self) -> bool:
        return bool(self._listen_task)

    @property
    def download_man(self) -> Optional[DownloadManager]:
        return self._download
