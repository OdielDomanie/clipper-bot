from collections import Counter
from ... import STREAMS_DB
from ...persistent_dict import PersistentDict
from .base import Stream, StreamWithActDL


# {unique_id: Stream}
all_streams = PersistentDict[object, "Stream"](
    STREAMS_DB, "all_stream",  pickling=True
)


# actdl_usage = Counter[StreamWithActDL]()

# def start_active_dl(s: StreamWithActDL):
