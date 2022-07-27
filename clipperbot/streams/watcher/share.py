import asyncio as aio
import logging
from typing import Callable, Type

from ..stream.base import Stream
from . import Sharer, watchers
from .base import Watcher
from .ttv import TtvWatcher
from .yt_chn import YtChnWatcher
from .yt_stream import YtStrmWatcher


logger = logging.getLogger(__name__)


class WatcherSharer:
    def __init__(self, watcher_class: Type[Watcher], target: str):
        "`target` is either a san url or a protocol string like `'collabs:<san_url>'`"
        self.target = target
        if target not in watchers:
            watcher = watcher_class(target)
            sharer = Sharer(watcher)
            watchers[target] = sharer
        self.sharer = watchers[target]
        self.sharer.usage += 1
        self.active = False

    def start(self, stream_start_hook: Callable[[Stream], None] | None):
        assert not self.active
        if self.sharer.start_count == 0:
            self.sharer.w.start()
        else:
            logger.info(f"Sharing watcher for {self.target}")
        self.sharer.start_count += 1
        if stream_start_hook:
            self.sharer.w.start_hooks[self] = stream_start_hook
        self.active = True

    def stop(self):
        assert self.active
        if self.sharer.start_count == 1:
            self.sharer.w.stop()
        else:
            logger.info(f"Stopping sharing watcher for {self.target}")
        self.sharer.start_count -= 1
        try:
            del self.sharer.w.start_hooks[self]
        except KeyError:
            pass

    @property
    def targets_url(self) -> str:
        return self.sharer.w.targets_url

    @property
    def name(self) -> str:
        return self.sharer.w.name

    def is_alias(self, name: str) -> bool:
        "Can be a partial alias."
        return self.sharer.w.is_alias(name)

    @property
    def active_stream(self) -> Stream | None:
        return self.sharer.w.active_stream

    @property
    def stream_on(self) -> aio.Event:
        return self.sharer.w.stream_on

    @property
    def stream_off(self) -> aio.Event:
        return self.sharer.w.stream_off


watcher_classes = (
    TtvWatcher,
    YtChnWatcher,
    YtStrmWatcher,
)


def create_watch_sharer(san_url: str) -> WatcherSharer:
    for W in watcher_classes:
        if W.url_is_valid(san_url):
            return WatcherSharer(W, san_url)
    logger.error("Valid Watcher not found.")
    raise ValueError("Valid Watcher not found.")
