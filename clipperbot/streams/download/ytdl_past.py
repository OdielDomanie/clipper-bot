from typing import Iterable, Optional, TypedDict
import yt_dlp


class _Section(TypedDict):
    start_time: float
    end_time: float


def _match_filter_notlive(info_dict, *, incomplete: bool) -> Optional[str]:
    if info_dict.get("is_live"):
        return "is_live"

def _match_filter_live(info_dict, *, incomplete: bool) -> Optional[str]:
    if not info_dict.get("is_live"):
        return "is_not_live"


def download_past(url: str, output: str, ss: float, t: float):
    def ranges(info_dict, *, ydl) -> Iterable[_Section]:
        return ({"start_time": ss, "end_time": ss + t},)
    with yt_dlp.YoutubeDL({
        "download_ranges": ranges,
        "quiet": True,
        "outtmpl": output,
        "match_filter": _match_filter_notlive,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
    }) as ydl:
        return ydl.download(url)


# Needs a yet unmerged commit to yt_dlp: https://github.com/yt-dlp/yt-dlp/issues/3451
def download_past_live(url: str, output: str, ss: float, t: float):
    # Assuming fragments are 1 second long. Not a solid assumption.
    live_from_start_seq = f"{int(ss)}-{int(t)}"
    with yt_dlp.YoutubeDL({
        "live_from_start":True,
        "live_from_start_seq": live_from_start_seq,
        "quiet": True,
        "outtmpl": output,
        "match_filter": _match_filter_live
    }) as ydl:
        return ydl.download(url)
