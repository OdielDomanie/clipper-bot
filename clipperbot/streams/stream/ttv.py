import asyncio as aio
import logging
import os
import pathlib
import re
import time
from typing import Any

from ...utils import INTRVL, find_intersections, lock, start_time_from_infodict
from ...vtuber_names import channels_list
from .. import cutting
from ..download.yt_live import YTLiveDownload
from ..download.ytdl_past import download_past
from . import all_streams
from .base import CantSseof, StreamStatus, StreamWithActDL


logger = logging.getLogger(__name__)


def ttv_stream_uid(info_dict: dict):
    return "ttv", info_dict["uploader_id"], info_dict["timestamp"]


class TTVStream(StreamWithActDL):

    @classmethod
    def url_is_valid(cls, url: str) -> bool:
        return bool(
            re.match(
                r"^https:\/\/www\.twitch\.tv\/[a-zA-Z0-9\-_]+$",
                url
            )
        or  re.match(
                r"^https://www\.twitch\.tv\/videos\/[0-9]+$",
                url
            )
        )

    def __init__(self, stream_url, title: str, online: StreamStatus, info_dict: dict):
        self.title = title
        self.actdl_off = aio.Event()
        self.actdl_off.set()
        self._online: StreamStatus | None = online
        self._info_dict = info_dict.copy()
        self._start_time = start_time_from_infodict(info_dict)
        self.unique_id = ttv_stream_uid(info_dict)

        self._download: YTLiveDownload | None = None
        self._download_task = None
        self._actdl_counter = 0
        self._past_actdl = list[tuple[float, YTLiveDownload]]()  # end time, download
        self._pastdl_lock = aio.Lock()
        self._past_segments_vod = list[tuple[int, int, str]]()
        self._clip_lock = aio.Lock()
        all_streams[self.unique_id] = self

    @property
    def stream_url(self):
        return self._info_dict["webpage_url"]

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
        assert self._download and self._download.download_task
        self._download.download_task.cancel()

    async def _download_till_end(self):
        output = os.path.join(self.download_dir, self.title + str(self._actdl_counter) +".ts")
        self._download = YTLiveDownload(self.stream_url, output)
        self.actdl_off.clear()
        non_cancel_exception = False
        try:
            assert self._download.download_task
            await self._download.download_task
            self._actdl_counter += 1
        except BaseException as e:
            if isinstance(e, Exception):
                non_cancel_exception = True
            raise
        finally:
            if not non_cancel_exception:
                self._past_actdl.append((time.time(), self._download))
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
            if self.online == StreamStatus.PAST:
                raise ValueError
            out_fpath = os.path.join(self.download_dir, self.title + f"{ss}_{t}.mp4")
            rt = await aio.to_thread(download_past, self.stream_url, out_fpath, ss, t)
            if rt:
                raise Exception(f"download_past{(self.stream_url, out_fpath, ss, t)} returned {rt}")
            self._past_segments_vod.append((ss, t, out_fpath))
            all_streams[self.unique_id] = self
            return out_fpath

    @lock("_clip_lock")
    async def _clip_ss(self, ts: float, duration: float, audio_only: bool) -> str:
        ts_irl = self.start_time + ts
        end_irl = ts_irl + duration
        out_fpath = os.path.join(self.download_dir, self.title + f"_{ts:.0f}_{duration:.0f}")

        for try_no in range(2):
            if self._download and self._download.start_time <= ts_irl:
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
                        try:
                            return await cutting.cut(
                                d.output_fpath,
                                ts_irl - d.start_time,
                                None,
                                duration,
                                out_fpath=out_fpath,
                                audio_only=audio_only,
                                quick_seek=True
                            )
                        except FileNotFoundError:
                            self._past_actdl.remove((d_end, d))
                            if try_no < 2:
                                logger.error(f"File {d.output_fpath} not found, trying clip again.")
                                continue
                            else:
                                raise

            # No live download can fully cover the clip.
            clip_intrv = (round(ts), round(ts+duration))

            vod_ints: list[tuple[str, INTRVL]] = [(d[2], (d[0], d[0]+d[1])) for d in self._past_segments_vod]
            vod_covered, vod_uncovered = find_intersections(clip_intrv, vod_ints)

            try:
                if self.online:
                    raise ValueError
                else:

                    add_s = list[tuple[str, tuple[int, int]]]()
                    for s in vod_uncovered:
                        dl_start = max(s[0]-30,0)
                        start_diff = s[0] - dl_start
                        dl_end = min(s[1], int(self.end_time)) if self.end_time else s[1]
                        end_diff = s[1] - dl_end
                        o = await self._download_past(dl_start, dl_end-dl_start)
                        add_s.append(
                            (o, (start_diff, end_diff))
                        )
                    return await cutting.concat(*(add_s + vod_covered), out_fpath=out_fpath)
            except Exception:
                for fpath, i in vod_ints:
                    if not os.path.isfile(fpath):
                        if try_no < 2:
                            logger.error(f"File {fpath} not found, trying clip again.")
                            vod_ints.remove((fpath, i))
                            continue
                        else:
                            raise
        assert False

    async def is_alias(self, name: str) -> bool:
        channel_id = self.info_dict["channel_url"].split("/")[-1]
        return (
            name in self.title
            or name in self.stream_url
            or name in self.info_dict["channel_url"]
            or any(
                name in name_ or (en_name and name in en_name)
                for chn_urls, name_, en_name in channels_list.values()
                if self.info_dict["channel_url"] in chn_urls
            )
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
            logger.info(f"Deleted: {path}")
            if li:
                li[0].remove(li[1])
            cleaned_size += stat.st_size
        all_streams[self.unique_id] = self
        return cleaned_size

    def used_files(self) -> list[str]:
        res = []
        if self._download:
            res.append(self._download.output_fpath)
        for end_time, d in self._past_actdl:
            res.append(d.output_fpath)
        for a, b, fpath in self._past_segments_vod:
            res.append(fpath)
        return res

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
        deep_del_key(state["_info_dict"], lambda i: isinstance(i, str) and i.startswith("_"))
        return state

    def __setstate__(self, state: dict):
        self.__dict__.update(state)
        self._download = None
        self._download_task = None
        self._pastdl_lock = aio.Lock()
        self._clip_lock = aio.Lock()
        self._online = None
