from typing import Type

from .base import Downloader
from .twitch import TwitchDownload
from .youtube import YTDownload

try:
    from .twspace import TwSpaceDownload
except ImportError:
    twspace_support = False
else:
    twspace_support = True


platforms: list[Type[Downloader]] = [
    YTDownload,
    TwitchDownload,
]
if twspace_support:
    platforms.append(TwSpaceDownload)  # type: ignore


def get_platform(url: str) -> tuple[Type[Downloader], str]:
    "Get a downloader class that supports the url and the sanitized url, or raise ValueError with a sendable message."
    for plat in platforms:
        try:
            san_url = plat.sanitize_url(url)
        except ValueError:
            pass
        else:
            return plat, san_url
    if twspace_support:
        err_msg = (
            "Only youtube, twitch, and twitter space urls are currently supported."
        )
    else:
        err_msg = "Only youtube and twitch are currently supported."
    raise ValueError(err_msg)
