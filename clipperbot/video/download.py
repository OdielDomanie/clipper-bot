import logging
import os
import sys
import asyncio
import shlex
import http
import datetime as dt
from datetime import timezone
import time
import functools
from urllib import parse
import dateutil.parser
import youtube_dl as ytdl
import aiohttp
import psutil
from .. import DOWNLOAD_DIR, YTDL_EXEC, POLL_INTERVAL


def sanitize_vid_url(vid_url):
    """`vid_url` can be a full url, or a youtube video id.
    Raises `ValueError` if `vid_url` not supported.
    Returns (full_vid_url, website) .
    """
    vid_url = vid_url.split("&")[0].split("#")[0]
    if "." not in vid_url:
        vid_url = "https://www.youtube.com/watch?v=" + vid_url
    vid_url = vid_url
    
    if "youtube.com/watch?v=" in vid_url:
        website = "youtube"
        return vid_url, website
    
    elif "twitch.tv/" in vid_url:
        website = "twitch"
        return vid_url, website
    
    else:
        raise ValueError("Only youtube.com or twitch.tv is supported.")


class StreamDownload:
    """A stream object for managing the download process.

    ### Usage:
    First initialize, then `start_download`.
    Delete when stream ends. 
    """
    def __init__(self, vid_url:str, title:str, start_time=None,
            download_dir=DOWNLOAD_DIR, ytdl_exec=YTDL_EXEC):
        """`vid_url` can be a full url, or a youtube video id.
        Raises `ValueError` if `vid_url` not supported.
        """
        self.vid_url, self.website = sanitize_vid_url(vid_url)
        self.title = title
        self.download_dir = download_dir
        file_name = vid_url[-10:] + "_" + title
        file_name = file_name.replace("/", "_")
        self.filepath = f"{os.path.join(self.download_dir, file_name)}.mp4"
        self.ytdl_exec = ytdl_exec

        self.logger = logging.getLogger("clipping.streams")
        self._proc_lock = asyncio.Lock()
        self.proc = None
        self.start_count = 0  # no of times start_download has been called.
        self.start_time = None  # When the download starts
        self.actual_start = start_time  # When the stream started at the original platform
        self.done = False

    async def start_download(self):
        """Starts download and stops process and returns when stream ends.
        Stops process when an exception is raised. (eg. cancelled)
        Calling this multiple times does not start new processes,
        but increments a counter instead.
        Process is stopped when the counter reaches 0.
        async safe.

        Can raise `RateLimited`, or another exception.
        """
        try:
            async with self._proc_lock:
                self.start_count += 1
                if self.proc is None or self.proc.returncode :
                    self.logger.info(f"Starting recording of {self.vid_url}")
                    await self._download()
                    self.wait_stop_task = asyncio.create_task(self._wait_stop())
                    self.start_time = dt.datetime.now()
                else:
                    self.logger.info(f"Sharing ({self.start_count}) download for"
                        f" {self.vid_url}")

                if self.website == "youtube":
                    video_id = self.vid_url.split("=")[-1]
                    try:
                        await self.get_holodex_start(video_id)
                    except Exception as exc:
                        self.logger.exception(exc)

            await asyncio.shield(self.wait_stop_task)

        except BaseException as e:
            if not isinstance(e, asyncio.CancelledError):
                self.logger.exception(e)
            if self.proc and self.proc.returncode is None:
                async with self._proc_lock:
                    self.start_count -= 1
                    if self.start_count == 0:
                        
                        try:
                            self.wait_stop_task.cancel()
                        except Exception as e:
                            logging.exception(e)
                        
                        await self.stop_process()
            raise e
    
    actual_start_cache = {}  # Slow memory leak
    async def get_holodex_start(self, video_id):
        if video_id not in StreamDownload.actual_start_cache:
            async with aiohttp.ClientSession() as session:
                base_url = "https://holodex.net/api/v2/videos/"
                url = parse.urljoin(base_url, video_id)
                async with session.get(url) as response:
                    resp = await response.json()
                    time_str = resp["start_actual"]
                    StreamDownload.actual_start_cache[video_id] = (
                        dateutil.parser.isoparse(time_str)
                    )
        self.actual_start = StreamDownload.actual_start_cache[video_id]

    async def _download(self):
        """Tries to start the stream download.
        Returns when file is created, or raises exception before.
        Can raise `ValueError`, `TimeoutError`, or `RateLimited`.
        Sets `self.proc` .
        """
        if  "youtube" == self.website or "twitch" == self.website:
        
            # try:
            #     os.remove(self.filepath)
            # except FileNotFoundError:
            #     os.remove(self.filepath + '.part')
        
            await self._yt_download()
        
        else:
            raise ValueError("Only youtube or twitch are supported.")

    async def _yt_download(self):
        yt_proc = await _yt_process(self.ytdl_exec, self.vid_url, self.filepath)
        self.proc = yt_proc
        self.logger.debug(f"Download process created.")

        #wait until the file is created or an error is thrown or for timeout
        TIMEOUT = 50
        yt_started_task = asyncio.create_task(_yt_started(yt_proc.stdout))
        yt_error = asyncio.create_task(_read_yt_error(yt_proc.stderr))

        done, pending = await asyncio.wait([yt_started_task, yt_error],
            timeout=TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
        
        if not done:  # timeout
            self.logger.error("yt-dl didn't output a download destination"
                " in {TIMEOUT} seconds. Stopping it.")
            for task_ in pending:
                task_.cancel()
            # terminate process
            await self.stop_recording()
            raise TimeoutError
        elif yt_error in done:
            yt_error.result()  # raise the exception
        elif yt_started_task in done:  # nominal operation
            f_path = yt_started_task.result()
    
    async def _wait_stop(self):
        "Stop process when stream ends or the process hangs."
        # Check if file size is increasing or if the file exists.
        POLL_INTV = 20
        file_size = 0
        while True:
            await asyncio.sleep(POLL_INTV)
            try:
                new_file_size = os.path.getsize(self.filepath + ".part")
            except FileNotFoundError:
                await self.stop_process()
                break
        
            if new_file_size == file_size:
                await self.stop_process()
                break
            else:
                file_size = new_file_size
    
    async def  stop_process(self):
        "Tries to terminate the process. Returns the returncode."
        WAIT_TIME = 20
        if self.proc.returncode is not None:
            self.logger.info("Process trying to be stopped is already done"
                f" with code {self.proc.returncode}")
        else:
            self.logger.debug("Terminating ytdl process.")

            # get the child ffmpeg process
            yt_dl = psutil.Process(self.proc.pid)
            yt_dl_child = yt_dl.children()[0]
            
            # This may not end the child
            self.proc.terminate()

            # Kill the child ffmpeg
            try:
                yt_dl_child.kill()
            except Exception as e:
                self.logger.exception(e)

            try:
                await asyncio.wait_for(asyncio.create_task(self.proc.wait()),
                    WAIT_TIME)
            except asyncio.TimeoutError:
                self.logger.critical("Process not dead 10 seconds after"
                    " killing it, continuing on.")
            else:
                self.logger.info(f"Process ended with {self.proc.returncode}")
        self.done = True
        return self.proc.returncode
    
    def __del__(self):
        if self.proc and self.proc.returncode is None:
            self.logger.error("Process still running, on object deletion."
                " Terminating.")
            self.proc.terminate()


_cache = {}
_lock_url = {}
async def wait_for_stream(channel_url:str, poll_interval=POLL_INTERVAL):
    """Returns when channel starts streaming.
    `channel_url` should be sanitized.
    Caches results for `poll_interval`.
    Returns `url, title, start_time`.
    Raises `ValueError` if channel_url not supported.
    Can raise `RateLimited`.
    """
    logger = logging.getLogger("clipping.fetch_stream")

    if channel_url not in _lock_url:
        _lock_url[channel_url] = asyncio.Lock()
    async with _lock_url[channel_url]:
        if (channel_url in _cache 
                and dt.datetime.now() - _cache[channel_url][1]
                < dt.timedelta(seconds=POLL_INTERVAL)):
            logger.debug(f"{channel_url} already checked recently."
                " Result: {_cache[channel_url][0]}")
            return _cache[channel_url][0]
        else:
            logger.debug(f"{channel_url} cache expired or none.")
            while True:
                logger.debug(f"Fetching for live stream of {channel_url}")
                if "youtube.com/" in channel_url:
                    try:
                        metadata_dict = await _fetch_yt_chn_stream(channel_url)
                    except RateLimited:
                        await asyncio.sleep(3600)
                        continue
                else:
                    raise ValueError(f"{channel_url} channel url is"
                        "not implemented.")
                if metadata_dict is not None:
                    url = metadata_dict["webpage_url"]
                    title = metadata_dict["title"][:-17]
                    
                    # Timestamp is returned for twitch
                    if timestamp := metadata_dict.get("timestamp"):
                        timestamp = dt.datetime.fromtimestamp(timestamp, timezone.utc)

                    _cache[channel_url] = ((url, title, timestamp), dt.datetime.now())
                    return url, title, timestamp
                else:
                    logger.debug(f"{channel_url} not live, sleeping"
                        f" {poll_interval} before trying again.")
                    await asyncio.sleep(poll_interval)


async def sanitize_chnurl(chn_url:str):
    """Returns the standardized url.
    Can raise `ValueError` if url not supported.
    Can raise `RateLimited`.
    """
    # May not be fully checking url validity.
    # TODO
    # !!This is may be security vulnerability!! Check the url properly! 
    if "." not in chn_url and "/" not in chn_url and chn_url != "":
        chn_url = "https://www.youtube.com/channel/" + chn_url
    if not chn_url.startswith("https://"):
        chn_url = "https://" + chn_url
    if "youtu.be" in chn_url:
        chn_url = chn_url.replace("youtu.be", "www.youtube.com")
    if "https://www.youtube.com/" in chn_url:
        chn_url = chn_url.split("?")[0].split("#")[0]  # remove url parameters
    if chn_url.startswith("https://www.youtube.com/c/"):  # custom url
        # channel url in the form of /channel/
        chn_id = (await _fetch_yt_chn_data(chn_url))["id"]
        chn_url = "https://www.youtube.com/channel/" + chn_id
    
    if not chn_url.startswith("https://www.youtube.com/channel/"):
        raise ValueError("Only Youtube channels are implemented.")

    return chn_url


@functools.lru_cache(maxsize=1000)
async def _fetch_yt_chn_data(chn_url):
    "Fetch youtube channel ìnfo_dict`. Cached."
    info_dict = await asyncio.to_thread(fetch_yt_metadata, chn_url)
    return info_dict


async def _fetch_yt_chn_stream(channel_url:str):
    """Return the metadata dict if stream is live, `None` otherwise.
    Can raise `RateLimited`. For youtube channels.
    """
    logger = logging.getLogger("clipping.fetch_channel_stream")
    metadata_dict = await asyncio.to_thread(fetch_yt_metadata, channel_url + "/live")

    if not metadata_dict:
        logger.log(5, f"No metadata received from {channel_url};"
            f" {channel_url} is not live.")
        return None
    else:
        if not metadata_dict.get("is_live"):
            logger.log(5, f"{channel_url} is not live.")
            return None
        else:
            stream_url = metadata_dict["webpage_url"]
            stream_title = metadata_dict["title"][:-17]  # title includes fetch date for same reason

            logger.log(5, f"{channel_url} is live with at {stream_url} / {stream_title}")
            return metadata_dict


class RateLimited(Exception):
    pass


repeated_errors = {}  # {url: [last_dtime, {err1, err2,}]}. Memory leak here!
def fetch_yt_metadata(url:str):
    """Fetches metadata of url, with `noplaylist`.
    Returns `info_dict`. Can raise `RateLimited`.
    """
    logger = logging.getLogger("clipping.fetch_metadata")

    ytdl_logger = logging.getLogger("ytdl_fetchinfo")
    ytdl_logger.addHandler(logging.NullHandler())  # f yt-dl logs

    # options referenced from https://github.com/sparanoid/live-dl/blob/3e76be969d94747aa5d9f83b34bc22e14e0929be/live-dl
    ydl_opts = {
        "logger" : ytdl_logger,
        "noplaylist" : True,
        "playlist_items": "0",
        "skip_download": True,
        "forcejson": True,
        "no_color": True,
        "referer": 'https://www.youtube.com/feed/subscriptions',
        "cookiefile": "cookies.txt",
    }
    try:
        with ytdl.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
    except ytdl.utils.DownloadError as e:
        # "<channel_name> is offline error is possible in twitch 
        if "This live event will begin in" in e.args[0] \
            or "is offline" in  e.args[0]:
            logger.debug(e)
        elif "HTTP Error 429" in e.args[0]:
            logger.critical(f"Got \"{e}\", for {url}.")
            raise RateLimited
        else:
            if url not in repeated_errors:
                repeated_errors[url] = [dt.datetime.now(), set()]
            REPEAT_LIMIT = dt.timedelta(minutes=30)
            if (e.args[0] not in repeated_errors[url][1] 
                    and dt.datetime.now() - repeated_errors[url][0] < REPEAT_LIMIT):
                logger.error(f"{e}, for {url}.")
                repeated_errors[url][1].add(e.args[0])
                repeated_errors[url][0] = dt.datetime.now()
        return None
    except http.cookiejar.LoadError as e:
        logger.error(f"Cookie error: {e}. Trying again")
        time.sleep(1)
        return fetch_yt_metadata(url)
    except http.HTTPError as e:  # never runs, catched as DownloadError
        if e.code == 429 or e.code == 403:
            logger.critical(f"Got \"{e}\"")
            raise RateLimited
    except Exception as e:
        logger.exception(e)
        return None
    return info_dict


async def _yt_process(ytdl_exec, url, filepath):
    """Starts a ytdl process download and return the process.
    Does not: Continues on the previous download.
    Deletes if previous file exists.
    """
    logger = logging.getLogger("clipping.streams")
    url_cmd = shlex.quote(url)
    filepath_cmd = shlex.quote(filepath)

    try:
        os.remove(filepath)
    except FileNotFoundError:
        pass
    try:
        os.remove(filepath + ".part")
    except FileNotFoundError:
        pass

    # no format is given, so ytdl chooses "best".
    # youtube-dl, for "best", seems to priotize:
    # mp4, highest resolution, highest fps, directly as a single file.
    # yt-dlp or another fork may have another way to choose "best", that
    # may break this code, especially in the case of a stream having a vp9
    # encoding option.
    # --continue
    cmd = f"{ytdl_exec} --no-cache-dir --hls-use-mpegts\
        --cookies cookies.txt -o {filepath_cmd} {url_cmd}"

    logger.info(f"Running: {shlex.join(shlex.split(cmd))}")

    yt_proc = await asyncio.create_subprocess_exec(*shlex.split(cmd),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    return yt_proc


async def _yt_started(stream:asyncio.StreamReader):
    """Wait until yt-dl creates the download file (proxy for starting download),
    then return the file path"""
    while line := await stream.readline():
        encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
        line = str(line, encoding)
        words = line.split(maxsplit=2)
        if words[0] == "[download]" and words[1] == "Destination:":
            f_path = words[2]
            asyncio.create_task(_stream_to_null(stream))
            return f_path.rstrip()


async def _stream_to_null(stream:asyncio.StreamReader):
    read = True
    while read:
        try:
            read = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError):
            read = True


async def _read_yt_error(stream:asyncio.StreamReader):
    "Read stderr until EOF or HTTP 429 is read, then `RateLimited` is raised."    
    while True:
        encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
        try:
            line = await stream.readline()
            try:
                line_str = str(line, encoding)
            except:
                line_str = '\n'

        except (ValueError, asyncio.LimitOverrunError):
            continue
        if not line:
            break
        if "HTTP Error 429:" in line_str:
            raise RateLimited
