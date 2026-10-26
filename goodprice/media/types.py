from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class MediaCandidate:
    key: str
    kind: Literal["image", "video"]
    url: str
    offer_key: str | None = None
    source_locator: str = ""
    poster_url: str | None = None
    status: str = "pending"


@dataclass(frozen=True)
class MediaEvidence:
    id: str
    kind: Literal["image", "frame"]
    content_hash: str
    local_path: Path
    offer_key: str | None = None
    video_id: str | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class MediaSelection:
    items: tuple[MediaEvidence, ...]
    omitted_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    width: int
    height: int


@dataclass(frozen=True)
class FrameFile:
    path: Path
    timestamp_ms: int
    content_hash: str
