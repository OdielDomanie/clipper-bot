import asyncio as aio
import logging
import os
import re
from typing import Optional

from requests import HTTPError
from twspace_dl.twspace_dl import FormatInfo, TwspaceDL

from ..exceptions import RateLimited
from .base import Listener
from .ytdl import YtdlListener

__all__ = ["TwSpaceListener"]


logger = logging.getLogger("clipping.listen")


class TwSpaceListener(YtdlListener):

    _poll_int = Listener.DEF_POLL_INTERVAL
    RECOVERY_FACTOR = 0.7  # Arbitrary

    @staticmethod
    def general_validate(url) -> bool:
        return bool(re.match(r"(https://)?(www\.)?(twitter\.com)/", url))

    @staticmethod
    def validate_url(url):
        # https://twitter.com/moricalliope
        return re.fullmatch(r"https://twitter\.com/[a-zA-Z0-9\-_]+", url)

    @classmethod
    async def sanitize_url(cls, url: str):
        if not cls.general_validate(url):
            raise ValueError("Invalid url.")

        if not url.startswith("https://"):
            url = "https://" + url

        url_split = url.split("/")
        if url_split[2].startswith("www."):
            url_split[2] = url_split[2][4:]
        url = "/".join(url_split)

        url_split = url.split("/")[:4]
        url = "/".join(url_split)
        if cls.validate_url(url):
            return url
        else:
            raise ValueError("Invalid url.")

    async def one_time(self) -> Optional[tuple[str, str, str, dict]]:
        try:
            space_dl = await aio.to_thread(
                TwspaceDL.from_user_tweets,
                self.chn_url,
                FormatInfo.DEFAULT_FNAME_FORMAT,
            )
        except RuntimeError as e:
            if e.args[0] == "User is not live":
                return None
            else:
                raise
        except HTTPError as e:
            if e.response.status_code == 420:
                self._poll_int *= 2
                raise RateLimited(self.chn_url, logger=logger) from e
            else:
                raise
        else:
            self._poll_int = max(
                self.DEF_POLL_INTERVAL, self._poll_int * self.RECOVERY_FACTOR
            )

        stream_url = "https://twitter.com/i/spaces/" + space_dl.id
        stream_title = f"{self.chn_url.split('/')[-1]}'s twitter space"
        output_fpath = os.path.join(self.download_dir, FormatInfo.DEFAULT_FNAME_FORMAT)
        info_dict = {"download_dir": self.download_dir}
        return stream_url, stream_title, output_fpath, info_dict
