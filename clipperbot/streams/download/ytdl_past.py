import logging
import time
from typing import Iterable, TypedDict

import yt_dlp

logger = logging.getLogger(__name__)


class _Section(TypedDict):
    start_time: float
    end_time: float


class CantDownload(Exception):
    pass


class OutOfTimeRange(CantDownload):
    def __init__(self, *args: object, infodict=None) -> None:
        super().__init__(*args)
        self.infodict = infodict


# Needs a yet unmerged commit to yt_dlp: https://github.com/yt-dlp/yt-dlp/issues/3451
# Can give this but still download:
# WARNING: [youtube] Unable to download webpage: HTTP Error 429: Too Many Requests
# ie_key = "Youtube"

def download_past(
    url: str, output: str, ss: int, t: int, *, info_dict=None
) -> tuple[dict, str]:
    "Returns info_dict, and either 'post_live' or 'processed'. Can raise CantDownload."
    if ss < 0 or t <= 0:
        raise ValueError()
    def ranges(info_dict: dict, ydl) -> Iterable[_Section]:
        if info_dict.get("live_status") == "is_live":
            return ()
        else:
            return ({"start_time": ss, "end_time": ss + t},)
    # Assuming fragments are 1 second long. Not a solid assumption.
    # Can calculate the times from the fragments list in extracted info,
    # but being fragment-exact is important.
    live_from_start_seq = f"{int(ss)}-{int(ss+t)}"

    t_start = time.perf_counter()

    params = {
        "download_ranges": ranges,  # using this with live_from_start break it
        "live_from_start":True,
        "live_from_start_seq": live_from_start_seq,
        "quiet": True,
        "outtmpl": output,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
        "noprogress": True,
        "cookiefile": "cookies.txt",
    }
    # The "bestvideo" formatting option doesn't work with twitch vods.
    if url.startswith("https://www.twitch.tv/videos/"):
        del params["format"]
    if "youtube.com/" in url:
        params["referer"] = "https://www.youtube.com/feed/subscriptions"

    with yt_dlp.YoutubeDL(params) as ydl:
        logger.info(f"Downloading past of {url}, {ss, t}")
        # if info_dict:
        #     extracted_info = info_dict
        # else:
        extracted_info = ydl.extract_info(url, download=True, process=True)  # These need to be true for live download to work
        # if extracted_info.get("live_status") == "post_live":
        #     live_status = "post_live"
        # elif extracted_info.get("live_status") == "is_live":
        #     live_status = "is_live"
        # else:
        #     live_status = "processed"
        live_status = extracted_info.get("live_status")  # type: ignore
            # ydl.params["download_ranges"] = ranges
        logger.info(f"{url}: {live_status}")
        if live_status == "post_live" and ss + t > 4 * 3600:  # yt-dlp issue #1564
            raise OutOfTimeRange(infodict=extracted_info)
        # ie_result = ydl.process_ie_result(extracted_info)
        logger.info(f"Download completed: {url, ss, t}")
        logger.info(f"Took {time.perf_counter() - t_start:.3f} s")
    return extracted_info, live_status  # type: ignore
