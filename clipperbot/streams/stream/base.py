import asyncio as aio
import enum
import logging
import os.path
import time
from typing import TYPE_CHECKING, overload, Literal
from abc import ABC, abstractmethod

from ... import CLIP_DIR, DOWNLOAD_DIR
from .. import cutting
from ..clip import Clip, Screenshot

if TYPE_CHECKING:
    from ..download.yt_live import YTLiveDownload


logger = logging.getLogger(__name__)


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

    @overload
    async def clip_from_start(
        self, ts: float, duration: float, audio_only: bool, screenshot: Literal[False] = False
    ) -> Clip:
        ...

    @overload
    async def clip_from_start(
        self, ts: float, duration: float, audio_only: bool, screenshot: Literal[True]
    ) -> Screenshot:
        ...

    async def clip_from_start(
        self, ts: float, duration: float, audio_only=False, screenshot=False
    ) -> Clip | Screenshot:
        if screenshot:
            png = await self._clip_ss(ts, duration, audio_only=audio_only, screenshot=screenshot)
            if len(png) < 200:
                raise Exception("Clip file probably corrupt.")
            else:
                name = f"{self.title}_{ts:.0f}.png"
                return Screenshot(
                    fname=name,
                    data=png,
                    ago=None,
                    from_start=ts
                )
        else:
            fpath = await self._clip_ss(ts, duration, audio_only=audio_only, screenshot=screenshot)
        size = os.path.getsize(fpath)
        SIZE_TRESHOLD = 20_000
        if size < SIZE_TRESHOLD:
            raise Exception("Clip file probably corrupt.")
        return Clip(
            fpath=fpath,
            size=size,
            duration=duration,
            ago=None,
            from_start=ts,
            audio_only=audio_only
        )

    @abstractmethod
    @overload
    async def _clip_ss(
        self, ts: float, duration: float, audio_only: bool, screenshot: Literal[False] = False,
    ) -> str:
        ...

    @abstractmethod
    @overload
    async def _clip_ss(
        self, ts: float, duration: float, audio_only: bool, screenshot: Literal[True]
    ) -> bytes:
        ...

    @abstractmethod
    async def _clip_ss(
        self, ts: float, duration: float, audio_only: bool, screenshot=False
    ) -> str | bytes:
        "Clip and return the file path, or the result as bytes."

    @overload
    async def clip_from_end(
        self, ago: float, duration: float, audio_only=False, screenshot: Literal[False] = False
    ) -> Clip:
        ...

    @overload
    async def clip_from_end(
        self, ago: float, duration: float, audio_only: bool, screenshot: Literal[True]
    ) -> Screenshot:
        ...

    async def clip_from_end(
        self, ago: float, duration: float, audio_only=False, screenshot=False
    ) -> Clip | Screenshot:

        ts =  (self.end_time or time.time()) - ago - self.start_time

        if screenshot:
            try:
                png = await self._clip_sseof(
                    ago, duration, audio_only=audio_only, screenshot=screenshot
                )
            except CantSseof:
                png = await self._clip_ss(
                    ts, duration, audio_only=audio_only, screenshot=screenshot
                )
            if len(png) < 200:
                raise Exception("Clip file probably corrupt.")
            name = f"{self.title}_{ts:.0f}.png"
            return Screenshot(
                fname=name,
                data=png,
                ago=ago,
                from_start=ts
            )

        try:
            fpath = await self._clip_sseof(
                ago, duration, audio_only=audio_only, screenshot=screenshot
            )
        except CantSseof:
            fpath = await self._clip_ss(
                ts, duration, audio_only=audio_only, screenshot=screenshot
            )
        size = os.path.getsize(fpath)
        SIZE_TRESHOLD = 20_000
        if size < SIZE_TRESHOLD:
            raise Exception("Clip file probably corrupt.")
        return Clip(
            fpath=fpath,
            size=size,
            duration=duration,
            ago=ago,
            from_start = ts,
            audio_only=audio_only
        )

    @abstractmethod
    @overload
    async def _clip_sseof(
        self, ago: float, duration: float, audio_only: bool, screenshot: Literal[False] = False
    ) -> str:
        ...

    @abstractmethod
    @overload
    async def _clip_sseof(
        self, ago: float, duration: float, audio_only: bool, screenshot: Literal[True]
    ) -> bytes:
        ...

    @abstractmethod
    async def _clip_sseof(
        self, ago: float, duration: float, audio_only: bool, screenshot=False
    ) -> str | bytes:
        "Clip and return the file path."

    @abstractmethod
    def is_alias(self, name: str) -> bool:
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

    @abstractmethod
    def stop_download(self):
        "Stop the live download."

    actdl_off: aio.Event
    actdl_on: aio.Event

    _download: "YTLiveDownload | None"
    _past_actdl: "list[tuple[float, YTLiveDownload]]"


class ClipFromLivedownload(StreamWithActDL):

    quick_seek: bool

    async def _clip_sseof(
        self, ago: float, duration: float, audio_only: bool, screenshot=False
    ):
        ts = time.time() - ago - self.start_time
        out_fpath = os.path.join(
            CLIP_DIR, self.title.replace("/","_") + f"_{ts:.0f}_{duration:.0f}"
        )
        if self._download and (time.time() - self._download.start_time) >= ago:
            if screenshot:
                return await cutting.screenshot(
                    self._download.output_fpath,
                    None,
                    -ago,
                    quick_seek=self.quick_seek,
                )
            else:
                return await cutting.cut(
                    self._download.output_fpath,
                    None,
                    -ago,
                    duration,
                    out_fpath,
                    audio_only,
                    quick_seek=self.quick_seek
                )
        else:
            raise CantSseof()

    async def _clip_ss(
        self, ts: float, duration: float, audio_only: bool, screenshot=False
    ):
        ts_irl = self.start_time + ts
        end_irl = ts_irl + duration
        out_fpath = os.path.join(
            CLIP_DIR, self.title.replace("/","_") + f"_{ts:.0f}_{duration:.0f}"
        )
        if screenshot:
            duration = 1
        clip_f = cutting.screenshot if screenshot else cutting.cut

        for try_no in range(3):  # If file is not found, try again
            continue_ = False
            # If covered by active download
            if self._download and self._download.start_time <= ts_irl:
                try:
                    return await clip_f(
                        self._download.output_fpath,
                        ts_irl - self._download.start_time,
                        None,
                        t=duration,
                        out_fpath=out_fpath,
                        audio_only=audio_only,
                        quick_seek=self.quick_seek
                    )
                except Exception as e:
                    if try_no < 2:
                        logger.info(f"Got {e}, trying again")
                    else:
                        raise
            else:
                for d_end, d in self._past_actdl:
                    if d.start_time <= ts_irl and end_irl <= d_end:
                        try:
                            return await clip_f(
                                d.output_fpath,
                                ts_irl - d.start_time,
                                None,
                                t=duration,
                                out_fpath=out_fpath,
                                audio_only=audio_only,
                                quick_seek=self.quick_seek
                            )
                        except FileNotFoundError:
                            self._past_actdl.remove((d_end, d))
                            if try_no < 2:
                                logger.error(f"File {d.output_fpath} not found, trying clip again.")
                                continue_ = True
                                break
                            else:
                                raise
            if continue_:
                continue

            # No live download can fully cover the clip.
            try:
                return await self._clip_from_segments(
                    ts,
                    duration,
                    audio_only,
                    screenshot=screenshot,
                    try_no=try_no,
                    out_fpath=out_fpath,
                )
            except Exception as e:
                if try_no >= 2:
                    raise
        assert False  # This is never reached.

    @abstractmethod
    async def _clip_from_segments(
        self,
        ts: float,
        duration: float,
        audio_only: bool,
        screenshot=False,
        *,
        out_fpath,
        try_no,
    ) -> str | bytes:
        """No live download can fully cover the clip.
        """
