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


class QueueStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
        return any(
            token in codec
            for token in ("pgs", "hdmv", "dvd_subtitle", "vobsub", "bdn", "dvb_subtitle", "xsub")
        )

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
class ReviewFrame:
    project_id: str
    frame_index: int
    start_ms: int
    end_ms: int
    status: str
    text: str = ""
    error: str = ""
    image_jpeg: bytes | None = None


@dataclass(frozen=True, slots=True)
class JobQualityReport:
    project_id: str
    total_frames: int
    recognized_frames: int
    recognized_lines: int
    empty_frames: int
    failed_frames: int
    overlaps: int
    suspicious_short: int
    suspicious_long: int
    adjacent_duplicates: int
    first_start_ms: int | None
    last_end_ms: int | None
    timing_status: str = "pending"

    @property
    def score(self) -> int:
        if self.total_frames <= 0:
            return 0
        failure_penalty = round(self.failed_frames * 60 / self.total_frames)
        structural_penalty = self.overlaps * 5
        duration_penalty = min(15, self.suspicious_short + self.suspicious_long)
        return max(0, min(100, 100 - failure_penalty - structural_penalty - duration_penalty))


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
    quality: JobQualityReport | None = None


@dataclass(frozen=True, slots=True)
class QueuedJob:
    id: int
    request: JobRequest
    status: QueueStatus
    position: int
    project_id: str = ""
    last_error: str = ""
