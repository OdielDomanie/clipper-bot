import asyncio as aio
import logging
import os
import re
from datetime import datetime
from typing import Iterable, Optional

import psutil
from twspace_dl.format_info import FormatInfo
from twspace_dl.twspace_dl import TwspaceDL

from ... import FFMPEG
from .base import Downloader

__all__ = ["TwSpaceDownload"]


logger = logging.getLogger("clipping.download")


class TwSpaceDownload(Downloader):
    """If `outpur_dir` is given, `output_fpath` is ignored and the default naming scheme is used.
    `output_dir` should not contain file extension.
    """

    def __init__(self, url: str, output_fpath: str, output_dir=None, **info_dict):
        assert self._validate(url)
        self.url = url
        if output_dir is None:
            self._output_fpath = output_fpath
        else:
            self._output_fpath = os.path.join(
                output_dir, FormatInfo.DEFAULT_FNAME_FORMAT
            )
        self.info_dict = info_dict

        self.temp_dir = None

        self._dl_task: Optional[aio.Task] = None

        self._start_time = None
        self._actual_start = info_dict.get("timestamp")

    @classmethod
    def sanitize_url(cls, url: str):
        "Sanitizes url to a standard. Raises `ValueError` if the url can't be used with this downloader."
        # https://twitter.com/i/spaces/123ABCxyz/peek

        try:
            if not url.startswith("https://"):
                url = "https://" + url

            url_split = url.split("/")

            if url_split[2].startswith("www."):
                url_split[2] = url_split[2][:4]

            if len(url_split) < 7:
                url_split.append("peek")
            else:
                url_split[6] = "peek"

            url = "/".join(url_split[:7])
        except IndexError as e:
            raise ValueError("Invalid url.") from e

        if cls._validate(url):
            return cls._validate(url)
        else:
            raise ValueError("Invalid url.")

    @staticmethod
    def _validate(url: str):
        return re.fullmatch(r"https://twitter\.com/i/spaces/[a-zA-Z0-9_]+", url)

    @property
    def output_fpath(self):
        if os.path.isfile(self._output_fpath + ".m4a"):
            return self._output_fpath + ".m4a"
        elif self.temp_dir is not None:
            for file in os.listdir(self.temp_dir):
                if file.endswith(".ts"):
                    return os.path.join(self.temp_dir, file)
        # Not found, default.
        return self._output_fpath + ".m4a"

    def start(self):
        """Returns a directory where the file might be, the final file destination,
        and the download task."""
        download_dir = os.path.dirname(self._output_fpath)
        space_dl = TwspaceDL.from_space_url(self.url, self.output_fpath, download_dir)
        self.temp_dir = space_dl.tmpdir
        # fpath = space_dl.filename

        self._dl_task = aio.create_task(self._twspace_download_process(space_dl))

    async def _twspace_download_process(self, space_dl: TwspaceDL):
        try:
            # Threads can't be cancelled, so we will wrap it in a shield to keep it simple.
            await aio.shield(aio.to_thread(space_dl.download))
        finally:
            if space_dl.ffmpeg_pid is not None:
                try:
                    # This will end the thread in most cases.
                    ffmpeg_proc = psutil.Process(space_dl.ffmpeg_pid)
                    ffmpeg_proc.kill()
                except Exception:
                    pass

    @property
    def dl_task(self) -> Optional[aio.Task]:
        return self._dl_task

    @property
    def start_time(self) -> Optional[datetime]:
        return self._start_time

    @property
    def actual_start(self) -> Optional[datetime]:
        return self._actual_start or self._start_time

    def clear_space(self):
        try:
            os.remove(self.output_fpath)
        except FileNotFoundError:
            pass

    def clip_args(
        self,
        clip_fpath: str,
        *,
        sseof: Optional[float] = None,
        ss: Optional[float] = None,
        duration: float,
        audio_only=True,
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
            "-vn",
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
