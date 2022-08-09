import asyncio as aio
from collections import Counter
import logging
import os

from ... import DOWNLOAD_DIR, MAX_DL_SIZE, STREAMS_DB
from ...persistent_dict import PersistentDict
from .base import Stream, StreamWithActDL


logger = logging.getLogger(__name__)


# {unique_id: Stream}
all_streams = PersistentDict[object, "Stream"](
    STREAMS_DB, "all_stream",  pickling=True
)


_stream_downloads = Counter()

def start_download(s: StreamWithActDL):
    "Start the download, or increment the counter if already started."
    if _stream_downloads[s.unique_id] == 0:
        s.start_download()
    _stream_downloads[s.unique_id] += 1


def stop_download(s: StreamWithActDL):
    "Stop the download, or decrement the counter if counter > 1."
    if _stream_downloads[s.unique_id] == 1:
        s.stop_download()
    _stream_downloads[s.unique_id] -= 1


async def clean_space():
    "Periodically clean the download cache."
    POLL_PERIOD = 120
    directory = DOWNLOAD_DIR
    while True:
        try:
            files = [os.path.join(directory, f) for f in os.listdir(directory)]
            total_size = sum(os.path.getsize(f) for f in files if os.path.isfile(f))
            excess = total_size - MAX_DL_SIZE
            if excess > 0:
                used_files = set[str]()
                for s in all_streams.values():
                    used_files.update(s.used_files())
                for f in files:
                    if f not in used_files:
                        logger.warning(f"Found unexpected file {f}, deleting.")
                        size = os.path.getsize(f)
                        os.remove(f)
                        excess -= size
            if excess > 0:
                sorted_streams = sorted(
                    all_streams.values(),
                    key=lambda s: s.start_time
                )
                for s in sorted_streams:
                    if excess <= 0:
                        break
                    excess -= s.clean_space(excess)
        except Exception as e:
            logger.exception(e)
        await aio.sleep(POLL_PERIOD)
