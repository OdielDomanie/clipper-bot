import asyncio as aio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterable, Optional

__all__ = ["Downloader"]


class Downloader(ABC):
    @classmethod
    @abstractmethod
    def sanitize_url(cls, url: str) -> str:
        "Sanitizes url to a standard. Raises `ValueError` if the url can't be used with this downloader."
        ...

    @abstractmethod
    def __init__(self, url: str, output_fpath: str, **info_dict):
        ...

    @abstractmethod
    def start(self):
        """Starts the download."""
        ...

    @abstractmethod
    def clear_space(self) -> Any:
        """Clears some arbitrary space related to the download (eg. by deleting the file)."""
        ...

    @property
    @abstractmethod
    def dl_task(self) -> Optional[aio.Task]:
        "This task returns when download ends. Cancelling this task cancels the download."
        ...

    @property
    @abstractmethod
    def start_time(self) -> datetime:
        ...

    @property
    @abstractmethod
    def actual_start(self) -> datetime:
        ...

    @abstractmethod
    def clip_args(
        self,
        clip_fpath: str,
        *,
        sseof: Optional[float] = None,
        ss: Optional[float] = None,
        duration: float,
        audio_only: bool = False,
        ffmpeg=...,
    ) -> Iterable[str]:
        "Returns a list of ffmpeg args for clipping."
        ...
