import asyncio as aio
import logging
import os
import os.path
import random
import shlex
import sys
from asyncio import subprocess as sp
from typing import Iterable

from .. import FFMPEG


logger = logging.getLogger(__name__)


def _increment_nice():
    os.nice(1)


async def cut(
    source: str,
    ss: float | None,
    sseof: float | None,
    t: float,
    out_fpath: str,
    audio_only = False,
    quick_seek = True,
    ffmpeg=FFMPEG,
) -> str:
    """Cut the video and return the output path.
    out_fpath must not have the file extension.
    """
    assert ss or sseof
    source_name, source_ext = os.path.splitext(source)

    if source_ext != ".webm":
        # discord doesn't embed .aac but .m4a
        extension = ".m4a" if audio_only else ".mp4"
    else:
        # discord doesnt embed audio only webms
        extension = ".ogg" if audio_only else ".webm"

    out_fpath += extension

    logger.debug(
        f"Creating clip file from {source} to {out_fpath}.\n"
        f"From: {ss}"
        f"{' ('+str(sseof)+')' if sseof is not None else ''}"
        f" for {str(t)}"
    )

    command: list[str] = [
        ffmpeg, "-y", "hide_banner",
        "-i", source,
        "-acodec", "copy",
        "-movflags", "faststart",
        out_fpath
    ]
    if audio_only:
        command.insert(7, "-vn")
    else:
        command.insert(7, "-vcodec")
        command.insert(8, "copy")

    time_args_i = 3 if quick_seek else 5
    if quick_seek:
        if sseof:
            command.insert(time_args_i, "-sseof")
            command.insert(time_args_i + 1, str(sseof))
        else:
            command.insert(time_args_i, "-ss")
            command.insert(time_args_i + 1, str(ss))
        command.insert(time_args_i + 2, "-t")
        command.insert(time_args_i + 3, str(t))

    try:
        return await _clip_process(command, out_fpath)
    except Exception as e:
        if not os.path.isfile(source):
            raise FileNotFoundError()
        else:
            raise


async def _clip_process(command: Iterable[str], out_fpath: str) -> str:
    logger.info(f"Clip cmd: {shlex.join(command)}")
    process = await sp.create_subprocess_exec(
            *command,
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            preexec_fn=_increment_nice,
        )
    logger.debug("Clip process started.")
    ffmpeg_logger = logging.getLogger(logger.name + ".ffmpeg")
    encoding = sys.stdout.encoding if sys.stdout.encoding else "utf-8"

    ffmpeg_out, _ = await process.communicate()
    ffmpeg_logger.debug(str(ffmpeg_out, encoding))

    if (return_code := await process.wait()) == 0:
        logger.debug("Clip process finished with 0.")
    else:
        logger.error(f"Clip process ended with {return_code}")
        if os.path.isfile(out_fpath):
            logger.error("However, clip file exists. Trying to continue on")
        else:
            raise Exception("Clip not created.")
    return out_fpath


# This is a different function despite seeming like a generalized version of the above
# function, as this one is less flexible for ease of writing.
async def concat(
    *sources: tuple[str, tuple[float, float]],
    out_fpath: str,
    ffmpeg=FFMPEG,
) -> str:
    """Concatenate videos, given path, ss, t. Returns output path.
    out_fpath must not have file extension.
    """
    out_fpath += ".mp4"
    concat = []
    for s_path, intrv in sources:
        ss, end = intrv
        concat.extend((
            "file " + s_path,
            "inpoint " + str(ss),
            "outpoint " + str(end),
        ))

    concat_fpath = f".concat_{random.randrange(10_000_000)}"
    with open(concat_fpath, "w") as f:
        f.writelines(concat)

    command = ([
        ffmpeg, "-y", "hide_banner", "-f", "-concat",
        "-i", concat_fpath,
        "-c", "copy", "-movflags", "faststart", out_fpath
    ])
    try:
        return await _clip_process(command, out_fpath)
    finally:
        os.remove(concat_fpath)


async def screenshot(
    fpath: str,
    ss: float | None,
    sseof: float | None,
    quick_seek: bool,
    ffmpeg=FFMPEG,
) -> bytes:
    "Creates a png screenshots and returns it as bytes."

    assert ss or sseof

    if not os.path.isfile(fpath):
        logger.error(
            f"Screenshot could not be created:"
            f" {fpath} not found."
        )
        raise FileNotFoundError

    command: list[str] = [
        ffmpeg, "-n", "hide_banner",
        "-i", fpath,
        "-vframes", "1",
        "-c:v", "png",
        "f", "image2pipe", "-"
    ]

    time_args_i = 3 if quick_seek else 5
    if quick_seek:
        if sseof:
            command.insert(time_args_i, "-sseof")
            command.insert(time_args_i + 1, str(sseof))
        else:
            command.insert(time_args_i, "-ss")
            command.insert(time_args_i + 1, str(ss))

    logger.info(shlex.join(command))

    process = await aio.create_subprocess_exec(
        *command,
        stdout=aio.subprocess.PIPE,
        stderr=aio.subprocess.PIPE,
    )

    ffmpeg_out, ffmpeg_err = await process.communicate()

    ffmpeg_logger = logging.getLogger(logger.name + ".ffmpeg")
    encoding = sys.stderr.encoding or "utf-8"
    ffmpeg_logger.debug(str(ffmpeg_err, encoding))

    return_code = await process.wait()

    if return_code == 0:
        return ffmpeg_out
    else:
        logger.error(
            f"Screenshot process for {fpath} failed with"
            f" {return_code}."
        )

        PNG_SIZE_TRESHOLD = 10_000
        if len(ffmpeg_out) >= PNG_SIZE_TRESHOLD:
            logger.info("However, some data was output, trying to continue.")
            return ffmpeg_out

        raise Exception("Screenshot not created.")
