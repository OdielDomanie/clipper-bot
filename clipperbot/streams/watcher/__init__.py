import typing

if typing.TYPE_CHECKING:
    from .base import Watcher


class Sharer:
    def __init__(self, w: Watcher):
        self.w = w
        self.usage = 0
        self.start_count = 0


# {target: (usage, watcher)}
watchers = dict[str, Sharer]()
