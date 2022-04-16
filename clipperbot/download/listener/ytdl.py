import logging

from .base import Listener

logger = logging.getLogger("clipping.listen")


__all__ = ["YtdlListener"]


class YtdlListener(Listener):

    _poll_int = Listener.DEF_POLL_INTERVAL
    RECOVERY_FACTOR = 0.7  # Arbitrary

    def _get_poll_interval(self) -> float:
        return self._poll_int
