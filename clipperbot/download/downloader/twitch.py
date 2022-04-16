import asyncio as aio
import logging
import re
from typing import Iterable, Optional

from ... import FFMPEG
from .ytdl import YtdlDownload, read_yt_error

logger = logging.getLogger("clipping.download")


class TwitchDownload(YtdlDownload):
    @classmethod
    def sanitize_url(cls, url: str):
        "Sanitizes url to a standard. Raises `ValueError` if the url can't be used with this downloader."
        if url.endswith("/"):
            url = url[:-1]
        if not url.startswith("https://"):
            url = "https://" + url
        url_split = url.split("/")
        if not url_split[2].startswith("www"):
            url_split[2] = "www." + url_split[2]
        url = "/".join(url_split[:4])
        if cls._validate(url):
            return cls._validate(url)
        else:
            raise ValueError("Invalid url.")

    @staticmethod
    def _validate(url: str):
        return re.fullmatch(r"https://www\.twitch\.tv/[a-z0-9_]+", url)

    async def _download(self):
        yt_proc = await self._start_download()
        read_for_error_task = aio.create_task(read_yt_error(yt_proc.stderr, self.url))  # type: ignore

        try:
            await self._wait_end(yt_proc)
        finally:
            read_for_error_task.cancel()
            await aio.gather(read_for_error_task)

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
            args.insert(8, "-ss")
            args.insert(9, f"{ss:.3f}")
        else:
            args.insert(3, "-sseof")
            args.insert(4, f"{sseof:.3f}")

        return args
