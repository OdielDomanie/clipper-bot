from typing import Type

from .base import Listener
from .twitch import TwitchListener
from .youtube import YoutubeListener
from .yt_stream import YtStreamListener

try:
    from .twspace import TwSpaceListener
except ImportError:
    twspace_support = False
else:
    twspace_support = True


platforms: list[Type[Listener]] = [
    YoutubeListener,
    TwitchListener,
    YtStreamListener,
]
if twspace_support:
    platforms.append(TwSpaceListener)  # type: ignore


async def get_listener(url: str) -> tuple[Type[Listener], str]:
    "Get a listener class that supports the url and a sanitized url, or raise ValueError with a sendable message."
    for plat in platforms:
        if not plat.general_validate(url):
            continue
        try:
            san_url = await plat.sanitize_url(url)
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
