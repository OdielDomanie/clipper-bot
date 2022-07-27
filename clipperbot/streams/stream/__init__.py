from ... import STREAMS_DB
from ...persistent_dict import PersistentDict
from .base import Stream


# {unique_id: Stream}
all_streams = PersistentDict[object, "Stream"](
    STREAMS_DB, "all_stream",  pickling=True
)
