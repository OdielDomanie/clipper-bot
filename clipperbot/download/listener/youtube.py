import logging
import os
import re
from typing import Optional

from ..exceptions import RateLimited
from ..extractors import fetch_yt_metadata
from .base import Listener
from .ytdl import YtdlListener

__all__ = ["YoutubeListener"]


logger = logging.getLogger("clipping.listen")


class YoutubeListener(YtdlListener):

    _poll_int = Listener.DEF_POLL_INTERVAL
    RECOVERY_FACTOR = 0.7  # Arbitrary

    @staticmethod
    def general_validate(url) -> bool:
        return bool(
            re.match(r"(https://)?(www\.)?((youtube\.com)|(youtu.be))/c(hannel)?/", url)
        )

    @staticmethod
    def validate_url(url):
        # https://www.youtube.com/channel/UChgTyjG-pdNvxxhdsXfHQ5Q
        return re.fullmatch(
            r"https://www\.youtube\.com/channel/[a-zA-Z0-9\-_]{24}", url
        )

    @staticmethod
    async def _fetch_full(short_url: str):
        """Return full youtube channel url from a youtube channel url. Can raise `ValueError` if fetching was unsuccessful,
        or `RateLimited`.
        """
        try:
            chn_id = (await fetch_yt_metadata(chn_url))["id"]  # type: ignore
        except (KeyError, AttributeError) as e:
            raise ValueError() from e
        chn_url = "https://www.youtube.com/channel/" + chn_id
        return chn_url

    @classmethod
    async def sanitize_url(cls, url: str):
        if not cls.general_validate(url):
            raise ValueError("Invalid url.")

        if not url.startswith("https://"):
            url = "https://" + url

        url_split = url.split("/")
        url_split[2] = url_split[2].replace("youtu.be", "youtube.com")
        if not url_split[2].startswith("www"):
            url_split[2] = "www." + url_split[2]
        url = "/".join(url_split)

        # https://www.youtube.com/c/LuaAsuka
        if match := re.match(r"https://www\.youtube\.com/c/[a-zA-Z0-9\-_]+", url):
            short_url = match.group()
            try:
                url = await cls._fetch_full(short_url)
            except ValueError as e:
                raise ValueError("Invalid url.") from e
            except RateLimited as e:
                cls._poll_int *= 2
                raise
            else:
                cls._poll_int = max(
                    cls.DEF_POLL_INTERVAL, cls._poll_int * cls.RECOVERY_FACTOR
                )

        url_split = url.split("/")[:7]
        url = "/".join(url_split)
        if cls.validate_url(url):
            return url
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
