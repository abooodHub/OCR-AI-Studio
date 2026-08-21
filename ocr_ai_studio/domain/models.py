from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class EngineKind(str, Enum):
    LM_STUDIO = "lmstudio"
    OLLAMA = "ollama"
    UNSLOTH = "unsloth"
    CUSTOM = "custom"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StreamInfo:
    ordinal: int
    source_index: int
    codec: str
    language: str = "und"
    title: str = ""

    @property
    def is_bitmap(self) -> bool:
        codec = self.codec.lower()
        return any(token in codec for token in ("pgs", "hdmv", "dvd_subtitle", "vobsub"))

    @property
    def is_text(self) -> bool:
        return not self.is_bitmap


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    frame_index: int | None = None

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise ValueError("start_ms cannot be negative")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if not self.text.strip():
            raise ValueError("subtitle text cannot be empty")


@dataclass(frozen=True, slots=True)
class JobRequest:
    input_path: Path
    output_path: Path
    stream: StreamInfo
    engine: EngineKind
    base_url: str
    model: str
    export_format: str = "srt"


@dataclass(frozen=True, slots=True)
class JobResult:
    project_id: str
    status: JobStatus
    total_frames: int = 0
    completed_frames: int = 0
    failed_frames: int = 0
    output_path: Path | None = None
    message: str = ""
