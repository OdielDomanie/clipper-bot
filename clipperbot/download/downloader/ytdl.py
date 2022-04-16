import asyncio as aio
import logging
import os
import shlex
import sys
from abc import abstractmethod
from asyncio.subprocess import Process
from datetime import datetime, timezone
from typing import Optional, Union

import psutil

from ... import YTDL_EXEC
from ..exceptions import RateLimited
from .base import Downloader

__all__ = ["YtdlDownload"]


logger = logging.getLogger("clipping.download")


async def read_yt_error(stream: aio.StreamReader, url: str):
    "Read stderr until EOF or HTTP 429 is read, then `RateLimited` is raised."
    while True:
        encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
        try:
            line = await stream.readline()
            try:
                line_str = str(line, encoding)
            except Exception:
                line_str = "\n"

        except (ValueError, aio.LimitOverrunError):
            continue
        if not line:
            break
        if "HTTP Error 429:" in line_str:
            raise RateLimited(url, logger=logger)


async def _stream_to_null(stream: aio.StreamReader):
    read: Union[bool, bytes] = True
    while read:
        try:
            read = await stream.readline()
        except (ValueError, aio.LimitOverrunError):
            read = True


async def _yt_started(stream: aio.StreamReader):
    """Wait until yt-dl creates the download file (proxy for starting download),
    then return the file path.
    """
    while line := await stream.readline():
        encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
        line_str = str(line, encoding)
        words = line_str.split(maxsplit=2)
        if words[0] == "[download]" and words[1] == "Destination:":
            f_path = words[2]
            aio.create_task(_stream_to_null(stream))
            return f_path.rstrip()


class YtdlDownload(Downloader):
    def __init__(self, url: str, output_fpath: str, **info_dict):
        assert self._validate(url)
        self.url = url
        self._output_fpath = output_fpath
        self.info_dict = info_dict

        self._dl_task: Optional[aio.Task] = None

        self._start_time = None
        timestamp = info_dict.get("timestamp")
        if timestamp is None:
            self._actual_start = None
        else:
            self._actual_start = datetime.fromtimestamp(timestamp, timezone.utc)

    @staticmethod
    @abstractmethod
    def _validate(url: str):
        ...

    @property
    def output_fpath(self):
        if os.path.isfile(self._output_fpath + ".part"):
            return self._output_fpath + ".part"
        else:
            return self._output_fpath

    WATCH_FILE_POLL_INTV = 10

    async def _watch_file(self):
        "Returns when the output file stops increasing in size, or is deleted."
        file_size = 0
        while True:
            await aio.sleep(self.WATCH_FILE_POLL_INTV)
            try:
                new_file_size = os.path.getsize(self._output_fpath + ".part")
            except FileNotFoundError:
                logger.warning(f"File {self._output_fpath} not found.")
                break
            if new_file_size == file_size:
                break
            else:
                file_size = new_file_size

    async def _stop(self, yt_proc: Process):
        "Terminates the process."
        WAIT_TIME = 10
        if yt_proc.returncode is not None:
            logger.info(
                "Process trying to be stopped is already done"
                f" with code {yt_proc.returncode}"
            )
        else:
            logger.debug("Terminating ytdl process.")

            # get the child ffmpeg process
            yt_dl = psutil.Process(yt_proc.pid)
            children = yt_dl.children()

            # This may not end the child.
            yt_proc.terminate()

            for yt_dl_child in children:
                # Kill the child ffmpeg
                try:
                    yt_dl_child.kill()
                except Exception as e:
                    logger.exception(e)

            try:
                await aio.wait_for(aio.create_task(yt_proc.wait()), WAIT_TIME)
            except aio.TimeoutError:
                logger.critical(
                    "Process not dead 10 seconds after" " killing it, continuing on."
                )
            else:
                logger.info(f"Process ended with {yt_proc.returncode}")

    async def _wait_end(self, yt_proc: Process):
        "This task returns when download ends, and does the cleanup. Cancelling this task cancels the download."
        proc_wait = aio.create_task(yt_proc.wait())
        file_watch = aio.create_task(self._watch_file())

        done, pending = None, None
        try:
            done, pending = await aio.wait(
                (proc_wait, file_watch),
                return_when=aio.FIRST_COMPLETED,
            )
        except aio.CancelledError:
            await self._stop(yt_proc)
            raise
        finally:
            if done is not None and pending is not None:
                if proc_wait in done:
                    logger.info(
                        f"Download process for {self.url} ended with {yt_proc.returncode}"
                    )
                else:
                    # Process did not end, but there is some problem with it.
                    await self._stop(yt_proc)
            # Cleanup to not leave any tasks hanging.
            proc_wait.cancel()
            file_watch.cancel()
            await aio.gather(proc_wait, file_watch, return_exceptions=True)

    async def _start_download(self):
        """Starts a ytdl process download. Waits until the output file is created.
        Returns the process. Cleans up when an exception (like cancelling) occurs.
        Does not: Continues on the previous download.
        Deletes if previous file exists.
        """
        url_cmd = shlex.quote(self.url)
        filepath_cmd = shlex.quote(self._output_fpath)

        try:
            os.remove(self.output_fpath)
        except FileNotFoundError:
            pass

        # no format is given, so ytdl chooses "best".
        # youtube-dl, for "best", seems to priotize:
        # mp4, highest resolution, highest fps, directly as a single file.
        # yt-dlp or another fork may have another way to choose "best", that
        # may break this code, especially in the case of a stream having a vp9
        # encoding option.
        # --continue
        cmd = f"{YTDL_EXEC} --no-cache-dir --hls-use-mpegts\
            --cookies cookies.txt -o {filepath_cmd} {url_cmd}"

        logger.info(f"Running: {shlex.join(shlex.split(cmd))}")

        yt_proc = await aio.create_subprocess_exec(
            *shlex.split(cmd),
            stdin=aio.subprocess.PIPE,
            stdout=aio.subprocess.DEVNULL,
            stderr=aio.subprocess.PIPE,
        )
        try:
            output_fpath = await _yt_started(yt_proc.stdout)  # type: ignore
        except BaseException as e:
            if isinstance(e, Exception):
                logger.exception(e)
            await self._stop(yt_proc)
            raise

        self._start_time = datetime.now(timezone.utc)
        return yt_proc

    @abstractmethod
    async def _download(self):
        ...

    def start(self):
        self._dl_task = aio.create_task(self._download())

    @property
    def dl_task(self) -> Optional[aio.Task]:
        return self._dl_task

    @property
    def start_time(self) -> Optional[datetime]:
        return self._start_time

    @property
    def actual_start(self) -> Optional[datetime]:
        return self._actual_start or self._start_time

    def clear_space(self):
        try:
            os.remove(self.output_fpath)
        except FileNotFoundError:
            pass
