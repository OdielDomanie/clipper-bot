import re
import asyncio as aio
import logging
import os
import pathlib
import time
from typing import Any

from ...utils import INTRVL, find_intersections, lock, start_time_from_infodict
from ...vtuber_names import channels_list
from .. import cutting
from ..download.holodex import holodex_req
from ..download.yt_live import YTLiveDownload
from ..download.ytdl_past import download_past, download_past_live
from . import all_streams
from .base import CantSseof, Stream, StreamStatus, StreamWithActDL

logger = logging.getLogger(__name__)


# Not needed anymore.
async def _get_holodex_start(self, video_id):
    TIMEOUT = 5 * 60
    logger.info("Fetching data from holodex.")
    resp = await aio.wait_for(
        holodex_req("videos", video_id, {}), TIMEOUT
    )
    return resp["start_actual"]


def yt_stream_uid(stream_url: str):
    return "yt", stream_url[-11:]


class YTStream(StreamWithActDL):

    @classmethod
    def url_is_valid(cls, url: str) -> bool:
        return bool(re.match(
            r"^https:\/\/www\.youtube\.com\/watch\?v=[a-zA-Z0-9\-_]{11}$",
            url
        ))

    def __init__(self, stream_url, title: str, online: StreamStatus, info_dict: dict):
        self.unique_id = yt_stream_uid(stream_url)
        self.stream_url = stream_url
        self.title = title
        self.actdl_off = aio.Event()
        self.actdl_off.set()
        self._online: StreamStatus | None = online
        self._info_dict = info_dict.copy()
        self._start_time = start_time_from_infodict(info_dict)
        self._pastlive_dl_allowed = True

        self._download: YTLiveDownload | None = None
        self._download_task = None
        self._actdl_counter = 0
        self._past_actdl = list[tuple[float, YTLiveDownload]]()  # end time, download
        self._pastdl_lock = aio.Lock()
        self._past_segments_live = list[tuple[int, int, str]]()
        self._past_segments_vod = list[tuple[int, int, str]]()
        self._clip_lock = aio.Lock()
        all_streams[self.unique_id] = self

    @property
    def online(self):
        return self._online
    @online.setter
    def online(self, o: StreamStatus):
        self._online = o
        all_streams[self.unique_id] = self

    @property
    def info_dict(self):
        return self._info_dict
    @info_dict.setter
    def info_dict(self, o: dict):
        self._info_dict = o
        all_streams[self.unique_id] = self

    @property
    def pastlive_dl_allowed(self):
        return self._pastlive_dl_allowed
    @pastlive_dl_allowed.setter
    def pastlive_dl_allowed(self, o: bool):
        self._pastlive_dl_allowed = o
        all_streams[self.unique_id] = self

    @property
    def active(self) -> bool:
        return bool(self._download)

    @property
    def start_time(self) -> int:
        assert self._start_time  # with correct order of operations and well behaved ytdl, this should hold.
        return self._start_time

    def start_download(self):
        "Start the live download."
        assert not self._download_task
        self._download_task = aio.create_task(self._download_till_end())
        if not self._start_time:
            # Will be way off if started late. If started late, the info_dict must be
            # not early.
            self._start_time = int(time.time())

    def stop_download(self):
        assert self._download
        self._download.download_task.cancel()

    async def _download_till_end(self):
        output = os.path.join(self.download_dir, self.title + str(self._actdl_counter) +".ts")
        self._download = YTLiveDownload(self.stream_url, output)
        self.actdl_off.clear()
        try:
            await self._download.download_task
            self._past_actdl.append((time.time(), self._download))
            self._actdl_counter += 1
        finally:
            self._download = None
            all_streams[self.unique_id] = self
            self.actdl_off.set()

    def _get_download_loc_ts(self, ts: float, duration:float) -> tuple[str, float] | None:
        """Return video fpath, and relative ss. None if the clip can't be fully covered
        (except if it spills into the future.).
        """
        ts_irl = self.start_time + ts
        end_irl = ts_irl + duration

        if self._download and self._download.start_time <= ts_irl:
            return self._download.output_fpath, ts_irl - self._download.start_time
        else:
            for d_end, d in self._past_actdl:
                if d.start_time <= ts_irl and end_irl <= d_end:
                    return d.output_fpath, ts_irl - d.start_time
        return None

    async def _download_past(self, ss: int, t: int) -> str:
        async with self._pastdl_lock:
            if self.online == StreamStatus.ONLINE:
                if not self.pastlive_dl_allowed:
                    raise Exception()  # Not well handled, but should be rare anyway.
                out_fpath = os.path.join(self.download_dir, self.title + f"{ss}_{t}.mp4")
                rt = await aio.to_thread(download_past_live, self.stream_url, out_fpath, ss, t)
                if rt:
                    raise Exception(f"download_past_live{(self.stream_url, out_fpath, ss, t)} returned {rt}")
                self._past_segments_live.append((ss, t, out_fpath))
                return out_fpath
            elif self.online == StreamStatus.PAST:
                out_fpath = os.path.join(self.download_dir, self.title + f"{ss}_{t}.mp4")
                rt = await aio.to_thread(download_past, self.stream_url, out_fpath, ss, t)
                if rt:
                    raise Exception(f"download_past{(self.stream_url, out_fpath, ss, t)} returned {rt}")
                self._past_segments_vod.append((ss, t, out_fpath))
                all_streams[self.unique_id] = self
                return out_fpath
        assert False

    @lock("_clip_lock")
    async def _clip_ss(self, ts: float, duration: float, audio_only: bool) -> str:
        ts_irl = self.start_time + ts
        end_irl = ts_irl + duration
        out_fpath = os.path.join(self.download_dir, self.title + f"_{ts:.0f}_{duration:.0f}")

        if self._download and self._download.start_time <= ts_irl:
            ss = ts_irl - self._download.start_time
            return await cutting.cut(
                self._download.output_fpath,
                ts_irl - self._download.start_time,
                None,
                duration,
                out_fpath=out_fpath,
                audio_only=audio_only,
                quick_seek=True
            )
        else:
            for d_end, d in self._past_actdl:
                if d.start_time <= ts_irl and end_irl <= d_end:
                    return await cutting.cut(
                        d.output_fpath,
                        ts_irl - d.start_time,
                        None,
                        duration,
                        out_fpath=out_fpath,
                        audio_only=audio_only,
                        quick_seek=True
                    )
        # No live download can fully cover the clip.
        clip_intrv = (round(ts), round(ts+duration))

        vod_ints: list[tuple[str, INTRVL]] = [(d[2], (d[0], d[0]+d[1])) for d in self._past_segments_vod]
        vod_covered, vod_uncovered = find_intersections(clip_intrv, vod_ints)
        plive_ints: list[tuple[str, INTRVL]] = [(d[2], (d[0], d[0]+d[1])) for d in self._past_segments_live]
        plive_covered, plive_uncovered = find_intersections(clip_intrv, plive_ints)

        if self.online:
            add_s = [
                (await self._download_past(s[0]-30, s[1]-s[0]+30), (30, -30))
                for s in plive_uncovered
            ]
            a: INTRVL = (-4, 4)
            return await cutting.concat(*(add_s + plive_covered), out_fpath=out_fpath)  # type: ignore
        else:
            add_s = [
                (await self._download_past(s[0]-30, s[1]-s[0]+30), (30, -30)) for s in vod_uncovered
            ]
            return await cutting.concat(*(add_s + vod_covered), out_fpath=out_fpath)  # type: ignore


    async def is_alias(self, name: str) -> bool:
        channel_id = self.info_dict["channel_url"].split("/")[-1]
        return (
            name in self.title
            or name in self.stream_url
            or name in self.info_dict["channel_url"]
            or name in channels_list.get(channel_id, ())[1:]
        )

    async def _clip_sseof(self, ago: float, duration: float, audio_only: bool) -> str:
        ts = time.time() - ago - self.start_time
        out_fpath = os.path.join(self.download_dir, self.title + f"_{ts:.0f}_{duration:.0f}")
        if self._download and (time.time() - self._download.start_time) >= ago:
            return await cutting.cut(
                self._download.output_fpath,
                None,
                -ago,
                duration,
                out_fpath,
                audio_only,
                quick_seek=True
            )
        else:
            raise CantSseof()

    def clean_space(self, size: int) -> int:
        # Punching holes does not seem feasible, as ffmpeg has trouble with timestamps
        # of a .ts file with gaps in it. Fixing it would be complicated.
        cleaned_size = 0
        all_files = list[tuple[tuple[list, Any] | None, pathlib.Path, os.stat_result]]()
        if self._download:
            path = pathlib.Path(self._download.output_fpath)
            if path.is_file():
                all_files.append((None, path, path.stat()))

        for end_time, d in self._past_actdl:
            path = pathlib.Path(d.output_fpath)
            if path.is_file():
                all_files.append(((self._past_actdl, (end_time, d)), path, path.stat()))
            else:
                self._past_actdl.remove((end_time, d))
        for a, b, fpath in self._past_segments_live:
            path = pathlib.Path(fpath)
            if path.is_file():
                all_files.append(((self._past_segments_live, (a, b, fpath)), path, path.stat()))
            else:
                self._past_segments_live.remove((a, b, fpath))
        for a, b, fpath in self._past_segments_vod:
            path = pathlib.Path(fpath)
            if path.is_file():
                all_files.append(((self._past_segments_vod, (a, b, fpath)), path, path.stat()))
            else:
                self._past_segments_vod.remove((a, b, fpath))

        all_files.sort(key=lambda fps: fps[2].st_mtime, reverse=True)
        while cleaned_size < size or not all_files:
            li, path, stat = all_files.pop()
            path.unlink(missing_ok=True)
            if li:
                li[0].remove(li[1])
            cleaned_size += stat.st_size
        all_streams[self.unique_id] = self
        return cleaned_size

    #pickling
    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        stateful = (
            "_download",
            "_download_task",
            "_pastdl_lock",
            "_clip_lock",
            "_online",
        )
        for key in stateful:
            del state[key]
        return state

    def __setstate__(self, state: dict):
        self.__dict__.update(state)
        self._download = None
        self._download_task = None
        self._pastdl_lock = aio.Lock()
        self._clip_lock = aio.Lock()
        self._online = None
