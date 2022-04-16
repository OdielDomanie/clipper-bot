import asyncio as aio
import logging
from dataclasses import dataclass
from typing import Type

from .downloader.base import Downloader

__all__ = ["DownloadManager"]


logger = logging.getLogger("clipping.download")


@dataclass
class ResourceShare:
    download: Downloader
    share_counter: int = 0
    cancel_sent: bool = False
    lock: aio.Lock = aio.Lock()


all_downloads: dict[str, ResourceShare] = {}


class DownloadManager:
    "Start a download, sharing resources with other DownloadManager instances."

    def __init__(
        self, url: str, platform: Type[Downloader], output_fpath: str, info_dict=None
    ):
        self.url = url

        if info_dict is None:
            info_dict = {}

        self._res_share = all_downloads.setdefault(
            url,
            ResourceShare(platform(url, output_fpath, info_dict=info_dict)),
        )
        self._res_share.share_counter += 1
        self._download = self._res_share.download

    @property
    def download(self) -> Downloader:
        "The downloader instance."
        return self._download

    def start(self):
        "Start the download."
        if not self._download.dl_task:
            logger.info(f"Starting download for {self.url}")
            self._download.start()
        else:
            logger.info(f"Sharing download for {self.url}")

    def stop(self):
        "Stop the download. This may not end `.wait_end()`."
        assert self._download.dl_task, "Download not started."
        self._res_share.share_counter -= 1
        if self._res_share.share_counter == 0:
            logger.info(f"Stopping download for {self.url}")
            self._download.dl_task.cancel()

    async def wait_end(self):
        "Wait until the download ends. Cancelling this does not stop the download."
        assert self._download.dl_task, "Download not started."
        async with self._res_share.lock:
            await aio.gather(aio.shield(self._download.dl_task), return_exceptions=True)
