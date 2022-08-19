from dataclasses import dataclass

from .cutting import screenshot


@dataclass(eq=True, frozen=True)
class Clip:
    fpath: str
    size: int
    duration: float
    ago: float | None
    from_start: float
    audio_only: bool

    async def create_thumbnail(self) -> bytes:
        if self.audio_only:
            raise Exception("Can't make thumbnail of audio_only clip.")

        return await screenshot(self.fpath, ss=0, sseof=None, quick_seek=True)


@dataclass(eq=True, frozen=True)
class Screenshot:
    fname: str
    data: bytes
    ago: float | None
    from_start: float
