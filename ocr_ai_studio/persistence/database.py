from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

from ocr_ai_studio.config.settings import default_data_dir
from ocr_ai_studio.domain.models import JobRequest, JobStatus, SubtitleCue

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    input_path TEXT NOT NULL,
    input_size INTEGER NOT NULL,
    input_mtime_ns INTEGER NOT NULL,
    stream_ordinal INTEGER NOT NULL,
    stream_codec TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT NOT NULL,
    output_path TEXT NOT NULL,
    status TEXT NOT NULL,
    total_frames INTEGER NOT NULL DEFAULT 0,
    completed_frames INTEGER NOT NULL DEFAULT 0,
    failed_frames INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS frames (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(project_id, frame_index)
);

CREATE TABLE IF NOT EXISTS cues (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    PRIMARY KEY(project_id, frame_index)
);
"""


class ProjectDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_dir() / "projects.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def project_id(request: JobRequest) -> str:
        source = request.input_path.resolve()
        stat = source.stat()
        identity = "|".join(
            (
                str(source).casefold(),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(request.stream.ordinal),
                request.stream.codec.casefold(),
                request.engine.value,
                request.base_url.casefold(),
                request.model.casefold(),
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def create_or_resume(self, request: JobRequest) -> str:
        project_id = self.project_id(request)
        stat = request.input_path.stat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, input_path, input_size, input_mtime_ns, stream_ordinal,
                    stream_codec, engine, model, output_path, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    output_path=excluded.output_path,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    str(request.input_path.resolve()),
                    stat.st_size,
                    stat.st_mtime_ns,
                    request.stream.ordinal,
                    request.stream.codec,
                    request.engine.value,
                    request.model,
                    str(request.output_path.resolve()),
                    JobStatus.RUNNING.value,
                ),
            )
        return project_id

    def set_totals(self, project_id: str, total_frames: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE projects SET total_frames=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (total_frames, project_id),
            )

    def completed_frame_indexes(self, project_id: str) -> set[int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT frame_index FROM frames WHERE project_id=? AND status IN ('done', 'empty')",
                (project_id,),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def record_frame(
        self,
        project_id: str,
        frame_index: int,
        start_ms: int,
        end_ms: int,
        status: str,
        error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO frames(project_id, frame_index, start_ms, end_ms, status, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, frame_index) DO UPDATE SET
                    status=excluded.status, error=excluded.error
                """,
                (project_id, frame_index, start_ms, end_ms, status, error),
            )
            counts = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('done', 'empty') THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END)
                FROM frames WHERE project_id=?
                """,
                (project_id,),
            ).fetchone()
            connection.execute(
                """
                UPDATE projects SET completed_frames=?, failed_frames=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (counts[0] or 0, counts[1] or 0, project_id),
            )

    def save_cue(self, project_id: str, cue: SubtitleCue) -> None:
        if cue.frame_index is None:
            raise ValueError("frame_index is required for persisted OCR cues")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO cues(project_id, frame_index, start_ms, end_ms, text, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, frame_index) DO UPDATE SET
                    start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                    text=excluded.text, confidence=excluded.confidence
                """,
                (project_id, cue.frame_index, cue.start_ms, cue.end_ms, cue.text, cue.confidence),
            )

    def load_cues(self, project_id: str) -> list[SubtitleCue]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT frame_index, start_ms, end_ms, text, confidence
                FROM cues WHERE project_id=? ORDER BY start_ms, frame_index
                """,
                (project_id,),
            ).fetchall()
        return [
            SubtitleCue(
                frame_index=row["frame_index"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                text=row["text"],
                confidence=row["confidence"],
            )
            for row in rows
        ]

    def set_status(self, project_id: str, status: JobStatus, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE projects SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (status.value, error, project_id),
            )

    def recent_projects(self, limit: int = 20) -> Iterable[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return rows
