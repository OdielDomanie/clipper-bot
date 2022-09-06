from dataclasses import dataclass


@dataclass(eq=True, frozen=True)
class SentClip:
    fpath: str | None
    duration: float
    ago: float | None
    from_start: float
    audio_only: bool
    channel_id: int
    msg_id: int
    user_id: int
    stream_uid: object


@dataclass(eq=True, frozen=True)
class SentSS:
    ago: float | None
    from_start: float
    channel_id: int
    msg_id: int
    user_id: int
    stream_uid: object
