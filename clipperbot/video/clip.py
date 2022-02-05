import asyncio
import datetime as dt
import logging
import os
import shlex
import sys
from ..utils import timedelta_to_str, hour_floor_diff, clean_space
from .. import CLIP_DIR, MAX_CLIP_STORAGE, FFMPEG


logger = logging.getLogger("clipping.clip")


async def clip(stream_filepath:str, title:str,
        from_time:dt.timedelta, duration:dt.timedelta,
        stream_start_time:dt.datetime, audio_only=False, relative_start=None,
        website="youtube", clip_dir = CLIP_DIR, ffmpeg=FFMPEG):
    """Creates a clip file from `stream_filepath`.
    Returns path of the clip file.
    """
    title = title.replace("/", "_")

    if stream_filepath.rsplit(".", 1)[:-1] != "webm":
        extension = ".m4a" if audio_only else ".mp4"  # discord doesn't embed .aac but .m4a
    else:
        extension = ".ogg" if audio_only else ".webm"  # discord doesnt embed audio only webms

    time_stamp = timedelta_to_str(
        hour_floor_diff(stream_start_time) + from_time,
        millisecs=False)
    clip_filepath = os.path.join(clip_dir,
        f"{title} {time_stamp}_{duration.total_seconds():.2f}{extension}")
    
    quick_seek = website == "youtube"

    await cut_video(stream_filepath, from_time, duration,
        clip_filepath, audio_only, ffmpeg, relative_start=relative_start,
        quickseek=quick_seek)

    clean_space(CLIP_DIR, MAX_CLIP_STORAGE)

    return clip_filepath


async def cut_video(stream_filepath:str,
        from_time:dt.timedelta, duration:dt.timedelta,
        output_path:str, audio_only=False, ffmpeg=FFMPEG, relative_start=None,
        quickseek=False):
    """ Cuts a video file.
    """
    logger.debug(f"Creating clip file from {stream_filepath} to {output_path}.\n"
        f"From: {str(from_time)} {'('+str(relative_start)+') ' if relative_start is not None else ''}for {str(duration)}")

    # Check if the stream file has .part appended.
    stream_filepath += ".part"
    if not os.path.isfile(stream_filepath):
        stream_filepath = stream_filepath.rsplit(".", maxsplit=1)[0]
        if not os.path.isfile(stream_filepath):
            logger.error(f"Clip could not be created:"
                f" {stream_filepath} not found.")
            raise FileNotFoundError

    if not os.path.isfile(output_path):
        # Time argument before the -i for faster seeking,
        # and after for accuracy
        # -t after -i only seems to cause errors when t is longer than the vid
        # duration

        if relative_start is None:
            start_arg = f"-ss {from_time.total_seconds():.3f}"
        else:
            # This probably feels better for the user.
            delayed_start = relative_start.total_seconds() - 1
            start_arg = f"-sseof {delayed_start:.3f}"

        if quickseek:
            command = (f"{ffmpeg} -y -hide_banner\
                {start_arg}\
                -t {duration.total_seconds():.3f}\
                -i {shlex.quote(stream_filepath)}\
                -acodec copy\
                {'-vn' if audio_only else '-vcodec copy'}\
                -movflags faststart\
                {shlex.quote(output_path)}"
            )
        else:
            command = (f"{ffmpeg} -y -hide_banner\
                -t {duration.total_seconds():.3f}\
                -i {shlex.quote(stream_filepath)}\
                {start_arg}\
                -acodec copy\
                {'-vn' if audio_only else '-vcodec copy'}\
                -movflags faststart\
                {shlex.quote(output_path)}"
            )

        logger.info(f"Clip cmd: {shlex.join(shlex.split(command))}")

        process = await asyncio.create_subprocess_exec(*shlex.split(command),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

        logger.debug(f"Clip process started.")

        ffmpeg_logger = logging.getLogger(logger.name + ".ffmpeg")
        encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"

        ffmpeg_out, _ = await process.communicate()
        ffmpeg_logger.debug(str(ffmpeg_out, encoding))

        if (return_code := await process.wait()) == 0:
            logger.debug(f"Clip process finished with 0.")
        else:
            logger.error(f"Clip process ended with {return_code}")
            if os.path.isfile(output_path):
                logger.error(f"However, clip file exists. Trying to continue on")
            else:
                raise Exception("Clip not created.")


async def create_thumbnail(video_fpath:str, ffmpeg=FFMPEG):
    "Creates thumbnail from first frame of video on the same dir."
    thumbnail_fpath = video_fpath.rsplit(".", 1)[0] + ".jpg"

    cmd = f"{ffmpeg} -n -i {shlex.quote(video_fpath)} -vframes 1 -q:v 4\
        {shlex.quote(thumbnail_fpath)}"

    logger.info(shlex.join(shlex.split(cmd)))

    process = await asyncio.create_subprocess_exec(*shlex.split(cmd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    ffmpeg_logger = logging.getLogger(logger.name + ".ffmpeg")
    encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
    ffmpeg_out, _ = await process.communicate()
    ffmpeg_logger.debug(str(ffmpeg_out, encoding))

    return_code = await process.wait()
    if return_code == 0:
        return thumbnail_fpath
    else:
        logger.error(f"Thumbnail creation of {video_fpath} failed with"
            f" {return_code}.")
        if os.path.isfile(thumbnail_fpath):
            return thumbnail_fpath
        else:
            return None


CROP_STR = {
    "whole": "crop=in_w:in_h:0:0",
    "tl" : "crop=in_w/2:in_h/2:0:0",    
    "tr" : "crop=in_w/2:in_h/2:in_w/2:0",
    "bl" : "crop=in_w/2:in_h/2:0:in_h/2",
    "br" : "crop=in_w/2:in_h/2:in_w/2:in_h/2"
}

async def create_screenshot(stream_filepath:str, pos, relative_start:dt.timedelta, ffmpeg=FFMPEG) -> bytes:
    "Creates a png screenshots and returns it as bytes."

    # Check if the stream file has .part appended.
    stream_filepath += ".part"
    if not os.path.isfile(stream_filepath):
        stream_filepath = stream_filepath.rsplit(".", maxsplit=1)[0]
        if not os.path.isfile(stream_filepath):
            logger.error(f"Clip could not be created:"
                f" {stream_filepath} not found.")
            raise FileNotFoundError

    cmd = f"{ffmpeg} -n -sseof {relative_start.total_seconds()-1} -i {shlex.quote(stream_filepath)}\
        -vframes 1 -filter:v \"{CROP_STR[pos]}\" -c:v png -f image2pipe -"

    logger.info(shlex.join(shlex.split(cmd)))

    process = await asyncio.create_subprocess_exec(*shlex.split(cmd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    ffmpeg_out, ffmpeg_err = await process.communicate()

    ffmpeg_logger = logging.getLogger(logger.name + ".ffmpeg")
    encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"
    ffmpeg_logger.debug(str(ffmpeg_err, encoding))

    return_code = await process.wait()

    if return_code == 0:
        return ffmpeg_out
    else:
        logger.error(f"Thumbnail creation of {stream_filepath} failed with"
            f" {return_code}.")
        raise Exception("Screenshot not created.")
