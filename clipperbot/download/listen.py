import asyncio as aio
import logging
from dataclasses import dataclass
from typing import Optional, Type

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
        self._res_share.share_counter += 1
        self._listen = self._res_share.listener

    def start(self):
        "Start listening. Will download when the channel goes live."
        if not self._listen.is_listening:
            logger.info(f"Starting listening for {self.url}")
            self._listen.start()
        else:
            logger.info(f"Sharing listening for {self.url}")

    def stop(self):
        "Stop listening. Stop the download."
        assert self._listen.is_listening, "Listening not started."
        self._res_share.share_counter -= 1
        if self._res_share.share_counter == 0:
            logger.info(f"Stopping listening for {self.url}")
            self._listen.stop()

    @property
    def download(self) -> Optional[DownloadManager]:
        return self._listen.download_man
