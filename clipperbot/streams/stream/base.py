import asyncio as aio
import enum
import os.path
import time
from abc import ABC, abstractmethod

from ... import DOWNLOAD_DIR
from ..clip import Clip


class StreamStatus(enum.Enum):
    "bool value is true if online"
    ONLINE = 0
    PAST = 1
    FUTURE = 2

    def __bool__(self) -> bool:
        return not (self.value)


class CantSseof(Exception):
    pass


class Stream(ABC):
    # There should only be one instance per a stream video

    @classmethod
    @abstractmethod
    def url_is_valid(cls, url: str) -> bool:
        "If the url can be used to instantiate the class."
        ...

    @abstractmethod
    def __init__(self, stream_url, title: str, online: StreamStatus, info_dict: dict):
        ...

    @property
    @abstractmethod
    def start_time(self) -> float:
        "Best guess at start time. Might change over time."
        ...

    unique_id: object  # unique id
    stream_url: str  # This is the url as it happens. May not be unique (eg. twitch)
    channel_url: str
    title: str
    end_time : float | None = None
    # If the stream is currently online, but not necessarily downloading
    # None means unknown, and must be quickly updated
    online: StreamStatus | None
    # _download_start: float  # When the download started.
    info_dict: dict

    download_dir: str = DOWNLOAD_DIR

    @property
    @abstractmethod
    def active(self) -> bool:
        "If the stream is currently downloading."
        ...

    async def clip_from_start(self, ts: float, duration: float, audio_only=True) -> Clip:
        fpath = await self._clip_ss(ts, duration, audio_only=audio_only)
        size = os.path.getsize(fpath)
        return Clip(
            fpath=fpath,
            size=size,
            duration=duration,
            ago=None,
            from_start=ts,
            audio_only=audio_only
        )

    @abstractmethod
    async def _clip_ss(self, ts: float, duration: float, audio_only: bool) -> str:
        "Clip and return the file path."

    async def clip_from_end(self, ago: float, duration: float, audio_only=True) -> Clip:
        ts =  (self.end_time or time.time()) - ago - self.start_time
        try:
            fpath = await self._clip_sseof(ago, duration, audio_only=audio_only)
        except CantSseof:
            fpath = await self._clip_ss(ts, duration, audio_only=audio_only)
        size = os.path.getsize(fpath)
        return Clip(
            fpath=fpath,
            size=size,
            duration=duration,
            ago=ago,
            from_start = ts,
            audio_only=audio_only
        )

    @abstractmethod
    async def _clip_sseof(self, ago: float, duration: float, audio_only: bool) -> str:
        "Clip and return the file path."

    @abstractmethod
    async def is_alias(self, name: str) -> bool:
        "If this stream can be partially described by the name or url."

    @abstractmethod
    def clean_space(self, size: int) -> int:
        "Clean up space from the download cache, for min the `size`."

    @abstractmethod
    def used_files(self) -> list[str]:
        "List files that shouldn't be simply deleted."

    def __hash__(self) -> int:
        return hash(self.unique_id)

    def __eq__(self, __o: "Stream") -> bool:
        return self.unique_id == __o.unique_id

    def __str__(self) -> str:
        return f"{self.stream_url} ({self.title})"


class StreamWithActDL(Stream):
    @abstractmethod
    def start_download(self):
        "Start the live download."

    actdl_off: aio.Event
