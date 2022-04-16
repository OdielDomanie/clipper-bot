import asyncio as aio
import logging
import re
from typing import Iterable, Optional
from urllib import parse

import aiohttp
import dateutil.parser

from ... import FFMPEG, HOLODEX_TOKEN
from .ytdl import YtdlDownload, read_yt_error

logger = logging.getLogger("clipping.download")


class YTDownload(YtdlDownload):
    @classmethod
    def sanitize_url(cls, url: str):
        "Sanitizes url to a standard. Raises `ValueError` if the url can't be used with this downloader."
        if not url.startswith("https://"):
            url = "https://" + url
        url_split = url.split("/")
        url_split[2] = url_split[2].replace("youtu.be", "youtube.com")
        if not url_split[2].startswith("www"):
            url_split[2] = "www." + url_split[2]
        url = "/".join(url_split[:4])
        if cls._validate(url):
            return cls._validate(url)
        else:
            raise ValueError("Invalid url.")

    @staticmethod
    def _validate(url: str):
        return re.fullmatch(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9\-\_]+", url)

    async def _download(self):
        yt_proc = await self._start_download()
        read_for_error_task = aio.create_task(read_yt_error(yt_proc.stderr), self.url)  # type: ignore
        get_actstart_task = aio.create_task(self._get_holodex_start())

        try:
            await self._wait_end(yt_proc)
        finally:
            read_for_error_task.cancel()
            get_actstart_task.cancel()
            await aio.gather(read_for_error_task, get_actstart_task)

    def clip_args(
        self,
        clip_fpath: str,
        *,
        sseof: Optional[float] = None,
        ss: Optional[float] = None,
        duration: float,
        audio_only: bool = False,
        ffmpeg=FFMPEG,
    ) -> Iterable[str]:

        assert sseof is not None or ss is not None

        args = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-t",
            f"{duration:.3f}",
            "-i",
            self.output_fpath,
            "-acodec",
            "copy",
            "-vn" if audio_only else "-vcodec copy",
            "-movflags",
            "faststart",
            clip_fpath,
        ]

        if sseof is None:
            # Only ss is given
            start_arg = ["-ss", f"{ss:.3f}"]
        else:
            start_arg = ["-sseof", f"{sseof:.3f}"]

        args.insert(3, start_arg[0])
        args.insert(4, start_arg[1])

        return args

    async def _get_holodex_start(self):
        """Waits 5 minutes, then fetches `start_actual` from holodex.net and writes it to `self.actual_start`.
        Tries again in 5 minutes again if it fails.

        Holodex API License:
        https://holodex.stoplight.io/docs/holodex/ZG9jOjM4ODA4NzA-license
        """

        async with aiohttp.ClientSession() as session:
            video_id = self.url.split("=")[-1]
            while True:
                await aio.sleep(5 * 60)

                try:
                    logger.info("Fetching data from holodex.")
                    base_url = "https://holodex.net/api/v2/videos/"
                    url = parse.urljoin(base_url, video_id)
                    if HOLODEX_TOKEN:
                        headers = {"X-APIKEY": HOLODEX_TOKEN}
                    else:
                        headers = None
                    async with session.get(url, headers=headers) as response:
                        resp = await response.json()
                        time_str = resp["start_actual"]
                        self._actual_start = dateutil.parser.isoparse(time_str)
                        logger.info("Acquired start_actual from holodex.")
                        break

                except Exception as e:
                    logger.error(e)
