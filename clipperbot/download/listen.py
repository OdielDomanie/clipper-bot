import asyncio as aio
import logging
from dataclasses import dataclass
from typing import Callable, Coroutine, Iterable, Optional, Type

from .download import DownloadManager
from .listener.base import Listener

__all__ = ["ListenManager"]


logger = logging.getLogger("clipping.listen")


@dataclass
class ResourceShare:
    listener: Listener
    share_counter: int = 0
    cancel_sent: bool = False
    lock: aio.Lock = aio.Lock()


all_listens: dict[str, ResourceShare] = {}


class ListenManager:
    "Start a listen, sharing resources with other ListenManager instances."

    def __init__(self, url: str, platform: Type[Listener]):
        self.url = url

        self._res_share = all_listens.setdefault(
            url,
            ResourceShare(platform(url)),
        )
        self._listen = self._res_share.listener

    def start(
        self,
        begin_hooks: Iterable[Callable[[], Coroutine]] = tuple(),
        end_hooks: Iterable[Callable[[], Coroutine]] = tuple(),
    ):
        "Start listening. Will download when the channel goes live. Hooks will be called when the download starts."
        if not self._listen.is_listening:
            logger.info(f"Starting listening for {self.url}")
            self._res_share.share_counter += 1
            self._listen.start(begin_hooks, end_hooks)
        else:
            logger.info(f"Sharing listening for {self.url}")

    def stop(self):
        "Stop listening. Stop the download."
        assert self._listen.is_listening, "Listening not started."
        self._res_share.share_counter -= 1
        if self._res_share.share_counter == 0:
            logger.info(f"Stopping listening for {self.url}")
            self._listen.stop()

    # async def _one_time(self, hooks: Iterable[Callable[[], Coroutine]] = tuple()):
    #     if not self._listen.is_listening:
    #         logger.info(f"Starting listening for {self.url}")
    #         self._res_share.share_counter += 1
    #         self._listen.start(hooks)
    #     else:
    #         logger.info(f"Sharing listening for {self.url}")

    # def start_one_time(self, hooks: Iterable[Callable[[], Coroutine]] = tuple()):
    #     """Start listening. Will download when the channel goes live. Stop listening when the download stops.
    #     Hooks will be called when the download starts.
    #     """
    #     if not self._listen.is_listening:
    #         logger.info(f"Starting listening for {self.url}")
    #         self._res_share.share_counter += 1
    #         self._listen.start(hooks)
    #     else:
    #         logger.info(f"Sharing listening for {self.url}")

    @property
    def download(self) -> Optional[DownloadManager]:
        return self._listen.download_man

    @property
    def listener(self) -> Optional[Listener]:
        return self._listen

    def __repr__(self) -> str:
        if self.download and self.download.download.dl_task:
            if self.download.download.dl_task.done:
                download_stat = "Done."
            else:
                download_stat = "Download in progress."
        else:
            download_stat = "Not started."
        return f'"<{self.url}", {repr(download_stat)}'
