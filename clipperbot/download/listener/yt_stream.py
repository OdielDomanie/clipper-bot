import logging
import os
import re
from typing import Optional

from ..exceptions import RateLimited
from ..extractors import fetch_yt_metadata
from .base import Listener
from .ytdl import YtdlListener

__all__ = ["YtStreamListener"]


logger = logging.getLogger("clipping.listen")


class YtStreamListener(YtdlListener):

    _poll_int = Listener.DEF_POLL_INTERVAL
    RECOVERY_FACTOR = 0.7  # Arbitrary

    @staticmethod
    def general_validate(url) -> bool:
        return bool(
            re.match(r"(https://)?(www\.)?((youtube\.com)|(youtu.be))/watch\?v=", url)
        )

    @staticmethod
    def validate_url(url):
        # https://www.youtube.com/channel/UChgTyjG-pdNvxxhdsXfHQ5Q
        return re.fullmatch(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9\-\_]+", url)

    @classmethod
    async def sanitize_url(cls, url: str):
        "Sanitizes url to a standard. Raises `ValueError` if the url can't be used with this downloader."
        if not url.startswith("https://"):
            url = "https://" + url
        url_split = url.split("/")
        url_split[2] = url_split[2].replace("youtu.be", "youtube.com")
        if not url_split[2].startswith("www"):
            url_split[2] = "www." + url_split[2]
        url = "/".join(url_split[:4])
        if cls.validate_url(url):
            return cls.validate_url(url)
        else:
            raise ValueError("Invalid url.")

    async def one_time(self) -> Optional[tuple[str, str, str, dict]]:
        try:
            info_dict = await fetch_yt_metadata(self.chn_url + "/live")
        except RateLimited:
            self._poll_int *= 2
            raise
        else:
            self._poll_int = max(
                self.DEF_POLL_INTERVAL, self._poll_int * self.RECOVERY_FACTOR
            )

        if not info_dict or not info_dict.get("is_live"):
            return None

        try:
            stream_url = info_dict["webpage_url"]
            # title includes fetch date for some reason
            stream_title = info_dict["title"][:-17]
            file_name = stream_url[-10:] + "_" + stream_title
            file_name = file_name.replace("/", "_")
            output_fpath = f"{os.path.join(self.download_dir, file_name)}.mp4"
            return stream_url, stream_title, output_fpath, info_dict
        except KeyError:
            return None
