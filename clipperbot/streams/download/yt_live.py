import asyncio as aio
import logging
import os
import shlex
import sys
import time
from asyncio.subprocess import PIPE, create_subprocess_exec

import psutil

from ..exceptions import DownloadBlocked


logger = logging.getLogger(__name__)


async def _stream_to_null(stream: aio.StreamReader):
    read = True
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


async def _read_yt_error(stream: aio.StreamReader):
    "Read stderr until EOF or HTTP 429 is read, then `RateLimited` is raised."
    while True:
        encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
        try:
            line = await stream.readline()
            try:
                line_str = str(line, encoding)
            except Exception:
                line_str = '\n'

        except (ValueError, aio.LimitOverrunError):
            continue
        if not line:
            break

        if "HTTP Error 429:" in line_str:
            logger.critical(line_str)
        elif "HTTP Error 403:" in line_str:
            logger.critical(line_str)
        elif "warning" in line_str.lower() or "error" in line_str.lower():
            logger.warning(line_str)


class YTLiveDownload:

    start_time: float

    def __init__(self, url: str, output_fpath: str):
        "Start the download. Download path should have an .ts extension."
        self.url = url
        self.output_fpath = output_fpath

        self.download_task = aio.create_task(self._start_n_wait())
        self._read_error_task = None
        self.download_error: DownloadBlocked | None = None

    # @classmethod
    # async def start_download(cls, url: str, output_fpath: str) -> "YTLiveDownload":
    #     d = cls(url ,output_fpath)
    #     d.download_task = aio.create_task(d._start_n_wait())
    #     return d

    async def _download_proc(self):
        "Start the ytdl process and return it."
        args = [
            sys.executable, "-m", "yt_dlp",
            "--hls-use-mpegts",
            "--match-filter", "is_live",
            "--cookies", ".cookies.txt",
            "--fixup", "never",  # prevent fixupm3u8
            "--no-part",
            "-o", self.output_fpath,
            self.url
        ]
        proc = await create_subprocess_exec(
            *args, stdin=PIPE, stdout=PIPE, stderr=PIPE
        )
        logger.info(f"Starting ytdl process: {shlex.join(args)}")
        self._read_error_task = aio.create_task(_read_yt_error(proc.stderr))  # type: ignore
        try:
            await _yt_started(proc.stdout)  # type: ignore  # stdout won't be None
        except BaseException as e:
            logger.critical(e, exc_info=True)
            await self._stop_proc(proc)
            raise
        self.start_time = time.time()
        return proc

    async def _watch_file(self):
        "Returns when the output file stops increasing in size, or is deleted."
        WATCH_FILE_POLL_INTV = 5
        file_size = 0
        while True:
            await aio.sleep(WATCH_FILE_POLL_INTV)
            try:
                new_file_size = os.path.getsize(self.output_fpath)
            except FileNotFoundError:
                logger.warning(f"File {self.output_fpath} not found.")
                break
            if new_file_size == file_size:
                break
            else:
                file_size = new_file_size

    @staticmethod
    async def _stop_proc(yt_proc: aio.subprocess.Process):
        "Terminates the process."
        WAIT_TIME = 5
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
                    f"Process not dead {WAIT_TIME} seconds after" " killing it, continuing on."
                )
            else:
                logger.info(f"Process ended with {yt_proc.returncode}")


    async def _start_n_wait(self):
        "Start the download, return when done. Can be cancelled."
        try:
            proc = await aio.wait_for(self._download_proc(), timeout=20)

            proc_wait_task = aio.create_task(proc.wait())
            watch_file_task = aio.create_task(self._watch_file())
            try:
                done, pending = await aio.wait(
                    (proc_wait_task, watch_file_task), return_when=aio.FIRST_COMPLETED
                )
            except BaseException as e:
                if isinstance(e, aio.CancelledError):
                    logger.info(f"Live download for {self.url} is being cancelled.")
                else:
                    logger.exception(e)
                await self._stop_proc(proc)
                raise

            if proc_wait_task in pending:  # process did not end, but the file did.
                logger.info(f"Live download for {self.url} had its file end.")
                proc_wait_task.cancel()
                await self._stop_proc(proc)
            else:  # process ended
                watch_file_task.cancel()
                ret_code = proc_wait_task.result()
                logger.info(f"Live download for {self.url} had its proc end with {ret_code}.")
        finally:
            if self._read_error_task:
                try:
                    # Time out approach isn't really good but whatever.
                    await aio.wait_for(aio.shield(self._read_error_task), 5)
                except DownloadBlocked as e:
                    self.download_error = e
                except aio.TimeoutError:
                    logger.critical(f"read_error_task for {self.url} timed out.")

    #pickling
    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        del state["download_task"]
        del state["_read_error_task"]
        return state

    def __setstate__(self, state: dict):
        self.__dict__ = state
        self.download_task = None
        self._read_error_task = None

    def __repr__(self) -> str:
        return repr(self.__getstate__())
