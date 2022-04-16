import logging
import os
import re
from typing import Optional

from ..exceptions import RateLimited
from ..extractors import fetch_yt_metadata
from .base import Listener
from .ytdl import YtdlListener

logger = logging.getLogger("clipping.listen")


__all__ = ["TwitchListener"]


class TwitchListener(YtdlListener):

    _poll_int = Listener.DEF_POLL_INTERVAL
    RECOVERY_FACTOR = 0.7  # Arbitrary

    @staticmethod
    def general_validate(url) -> bool:
        return bool(re.match(r"(https://)?(www\.)?(twitch\.tv)/.", url))

    @staticmethod
    def validate_url(url):
        # https://www.twitch.tv/noxiouslive
        return re.fullmatch(r"https://www\.twitch\.tv/[a-z0-9\-_]+", url)

    @classmethod
    async def sanitize_url(cls, url: str):
        if not cls.general_validate(url):
            raise ValueError("Invalid url.")

        if not url.startswith("https://"):
            url = "https://" + url

        url_split = url.split("/")
        if not url_split[2].startswith("www"):
            url_split[2] = "www." + url_split[2]

        url_split[-1] = url_split[-1].lower()
        url = "/".join(url_split[:6])
        if cls.validate_url(url):
            return url
        else:
            raise ValueError("Invalid url.")

    async def one_time(self) -> Optional[tuple[str, str, str, dict]]:
        try:
            info_dict = await fetch_yt_metadata(self.chn_url)
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
            file_name = stream_url.split("/")[-1] + "_" + stream_title
            file_name = file_name.replace("/", "_")
            output_fpath = f"{os.path.join(self.download_dir, file_name)}.mp4"
            return stream_url, stream_title, output_fpath, info_dict
        except KeyError:
            return None
