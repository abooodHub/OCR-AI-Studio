from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

from ocr_ai_studio.config.settings import default_data_dir
from ocr_ai_studio.domain.models import (
    EngineKind,
    JobQualityReport,
    JobRequest,
    JobStatus,
    QueuedJob,
    QueueStatus,
    ReviewFrame,
    StreamInfo,
    SubtitleCue,
)

PGS_TIMING_REVISION = "pgs-copyts-v1"

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
    timing_status TEXT NOT NULL DEFAULT 'pending',
    timing_origin_status TEXT NOT NULL DEFAULT 'pending',
    timing_offset_ms INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS frames (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    source_start_ms INTEGER,
    source_end_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    image_jpeg BLOB,
    attempts INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    stream_ordinal INTEGER NOT NULL,
    stream_source_index INTEGER NOT NULL,
    stream_codec TEXT NOT NULL,
    stream_language TEXT NOT NULL DEFAULT 'und',
    stream_title TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    export_format TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class ProjectDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_dir() / "projects.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.execute(
                "UPDATE job_queue SET status='queued', updated_at=CURRENT_TIMESTAMP "
                "WHERE status='running'"
            )

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        project_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(projects)").fetchall()
        }
        if "timing_status" not in project_columns:
            connection.execute(
                "ALTER TABLE projects ADD COLUMN timing_status TEXT NOT NULL DEFAULT 'pending'"
            )
        if "timing_offset_ms" not in project_columns:
            connection.execute(
                "ALTER TABLE projects ADD COLUMN timing_offset_ms INTEGER NOT NULL DEFAULT 0"
            )
        if "timing_origin_status" not in project_columns:
            connection.execute(
                "ALTER TABLE projects ADD COLUMN timing_origin_status TEXT NOT NULL DEFAULT 'pending'"
            )
            connection.execute("UPDATE projects SET timing_origin_status=timing_status")
        frame_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(frames)").fetchall()
        }
        if "image_jpeg" not in frame_columns:
            connection.execute("ALTER TABLE frames ADD COLUMN image_jpeg BLOB")
        if "source_start_ms" not in frame_columns:
            connection.execute("ALTER TABLE frames ADD COLUMN source_start_ms INTEGER")
            connection.execute("UPDATE frames SET source_start_ms=start_ms")
        if "source_end_ms" not in frame_columns:
            connection.execute("ALTER TABLE frames ADD COLUMN source_end_ms INTEGER")
            connection.execute("UPDATE frames SET source_end_ms=end_ms")
        if "attempts" not in frame_columns:
            connection.execute("ALTER TABLE frames ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")

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
        related_identity: list[str] = []
        if source.is_dir():
            dvd_root = source / "VIDEO_TS" if (source / "VIDEO_TS").is_dir() else source
            for dvd_file in sorted(
                (
                    item
                    for item in dvd_root.iterdir()
                    if item.is_file() and item.suffix.casefold() in {".ifo", ".vob"}
                ),
                key=lambda item: item.name.casefold(),
            ):
                dvd_stat = dvd_file.stat()
                related_identity.extend(
                    (dvd_file.name.casefold(), str(dvd_stat.st_size), str(dvd_stat.st_mtime_ns))
                )
        elif source.suffix.lower() in {".sub", ".idx"}:
            companion_suffix = ".idx" if source.suffix.lower() == ".sub" else ".sub"
            companion = source.with_suffix(companion_suffix)
            if companion.is_file():
                companion_stat = companion.stat()
                related_identity.extend(
                    (str(companion).casefold(), str(companion_stat.st_size), str(companion_stat.st_mtime_ns))
                )
        elif request.stream.codec.casefold() == "bdn_xml":
            from ocr_ai_studio.media.bdn_parser import BDNParser

            for graphic in BDNParser(source).graphic_paths():
                graphic_stat = graphic.stat()
                related_identity.extend(
                    (str(graphic).casefold(), str(graphic_stat.st_size), str(graphic_stat.st_mtime_ns))
                )
        timing_revision = (
            PGS_TIMING_REVISION
            if "pgs" in request.stream.codec.casefold()
            or "hdmv" in request.stream.codec.casefold()
            else "default-timing-v1"
        )
        identity = "|".join(
            (
                str(source).casefold(),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(request.stream.ordinal),
                request.stream.codec.casefold(),
                request.stream.title.casefold(),
                request.engine.value,
                request.base_url.casefold(),
                request.model.casefold(),
                timing_revision,
                *related_identity,
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

    def set_timing_status(self, project_id: str, status: str) -> None:
        if status not in {"pending", "validated", "source_native", "manually_adjusted"}:
            raise ValueError(f"Unsupported timing status: {status}")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET timing_status=CASE
                        WHEN timing_status='manually_adjusted' THEN timing_status
                        ELSE ?
                    END,
                    timing_origin_status=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, status, project_id),
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
        image_jpeg: bytes | None = None,
        attempts: int = 0,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO frames(
                    project_id, frame_index, start_ms, end_ms, source_start_ms, source_end_ms,
                    status, error, image_jpeg, attempts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, frame_index) DO UPDATE SET
                    start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                    source_start_ms=COALESCE(frames.source_start_ms, excluded.source_start_ms),
                    source_end_ms=COALESCE(frames.source_end_ms, excluded.source_end_ms),
                    status=excluded.status, error=excluded.error,
                    image_jpeg=COALESCE(excluded.image_jpeg, frames.image_jpeg),
                    attempts=MAX(frames.attempts, excluded.attempts)
                """,
                (
                    project_id,
                    frame_index,
                    start_ms,
                    end_ms,
                    start_ms,
                    end_ms,
                    status,
                    error,
                    image_jpeg,
                    attempts,
                ),
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

    def review_frames(self, project_id: str, *, failures_only: bool = False) -> list[ReviewFrame]:
        where = "AND frames.status='failed'" if failures_only else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT frames.project_id, frames.frame_index, frames.start_ms, frames.end_ms,
                       frames.status, frames.error, frames.image_jpeg, COALESCE(cues.text, '') AS text
                FROM frames
                LEFT JOIN cues ON cues.project_id=frames.project_id
                              AND cues.frame_index=frames.frame_index
                WHERE frames.project_id=? {where}
                ORDER BY frames.frame_index
                """,
                (project_id,),
            ).fetchall()
        return [
            ReviewFrame(
                project_id=str(row["project_id"]),
                frame_index=int(row["frame_index"]),
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                status=str(row["status"]),
                text=str(row["text"]),
                error=str(row["error"] or ""),
                image_jpeg=bytes(row["image_jpeg"]) if row["image_jpeg"] is not None else None,
            )
            for row in rows
        ]

    def update_cue_text(self, project_id: str, frame_index: int, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Subtitle text cannot be empty")
        with self.connect() as connection:
            frame = connection.execute(
                "SELECT start_ms, end_ms FROM frames WHERE project_id=? AND frame_index=?",
                (project_id, frame_index),
            ).fetchone()
            if frame is None:
                raise ValueError("Review frame was not found")
            connection.execute(
                """
                INSERT INTO cues(project_id, frame_index, start_ms, end_ms, text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, frame_index) DO UPDATE SET text=excluded.text
                """,
                (project_id, frame_index, frame["start_ms"], frame["end_ms"], cleaned),
            )
            connection.execute(
                "UPDATE frames SET status='done', error=NULL WHERE project_id=? AND frame_index=?",
                (project_id, frame_index),
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

    def update_cue_timing(
        self, project_id: str, frame_index: int, start_ms: int, end_ms: int
    ) -> None:
        if start_ms < 0:
            raise ValueError("Subtitle start cannot be negative")
        if end_ms <= start_ms:
            raise ValueError("Subtitle end must be greater than its start")
        with self.connect() as connection:
            frame = connection.execute(
                "SELECT 1 FROM frames WHERE project_id=? AND frame_index=?",
                (project_id, frame_index),
            ).fetchone()
            if frame is None:
                raise ValueError("Review frame was not found")
            connection.execute(
                "UPDATE frames SET start_ms=?, end_ms=? WHERE project_id=? AND frame_index=?",
                (start_ms, end_ms, project_id, frame_index),
            )
            connection.execute(
                "UPDATE cues SET start_ms=?, end_ms=? WHERE project_id=? AND frame_index=?",
                (start_ms, end_ms, project_id, frame_index),
            )
            connection.execute(
                """
                UPDATE projects SET timing_status='manually_adjusted', updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (project_id,),
            )

    def shift_project_timing(self, project_id: str, offset_ms: int) -> int:
        if offset_ms == 0:
            return self.project_timing_offset(project_id)
        with self.connect() as connection:
            project = connection.execute(
                "SELECT timing_offset_ms, total_frames FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError("Project was not found")
            earliest = connection.execute(
                "SELECT MIN(start_ms) AS first_start FROM frames WHERE project_id=?",
                (project_id,),
            ).fetchone()
            stored_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM frames WHERE project_id=?", (project_id,)
                ).fetchone()[0]
            )
            expected_count = int(project["total_frames"] or 0)
            if expected_count <= 0 or stored_count < expected_count:
                raise ValueError("Finish indexing the project before shifting all subtitle timings")
            if earliest is None or earliest["first_start"] is None:
                raise ValueError("Project has no timed frames")
            if int(earliest["first_start"]) + offset_ms < 0:
                raise ValueError("Timing offset would move the first subtitle before 00:00:00")
            connection.execute(
                "UPDATE frames SET start_ms=start_ms+?, end_ms=end_ms+? WHERE project_id=?",
                (offset_ms, offset_ms, project_id),
            )
            connection.execute(
                "UPDATE cues SET start_ms=start_ms+?, end_ms=end_ms+? WHERE project_id=?",
                (offset_ms, offset_ms, project_id),
            )
            cumulative = int(project["timing_offset_ms"] or 0) + offset_ms
            connection.execute(
                """
                UPDATE projects
                SET timing_offset_ms=?, timing_status='manually_adjusted',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (cumulative, project_id),
            )
        return cumulative

    def project_timing_offset(self, project_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT timing_offset_ms FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Project was not found")
        return int(row["timing_offset_ms"] or 0)

    def restore_source_timing(self, project_id: str) -> None:
        with self.connect() as connection:
            project = connection.execute(
                "SELECT timing_origin_status FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError("Project was not found")
            missing = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM frames
                    WHERE project_id=? AND (source_start_ms IS NULL OR source_end_ms IS NULL)
                    """,
                    (project_id,),
                ).fetchone()[0]
            )
            if missing:
                raise ValueError("Original timing is unavailable for one or more frames")
            connection.execute(
                """
                UPDATE frames SET start_ms=source_start_ms, end_ms=source_end_ms
                WHERE project_id=?
                """,
                (project_id,),
            )
            connection.execute(
                """
                UPDATE cues
                SET start_ms=(
                        SELECT frames.source_start_ms FROM frames
                        WHERE frames.project_id=cues.project_id
                          AND frames.frame_index=cues.frame_index
                    ),
                    end_ms=(
                        SELECT frames.source_end_ms FROM frames
                        WHERE frames.project_id=cues.project_id
                          AND frames.frame_index=cues.frame_index
                    )
                WHERE project_id=?
                """,
                (project_id,),
            )
            connection.execute(
                """
                UPDATE projects SET timing_offset_ms=0, timing_status=timing_origin_status,
                                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (project_id,),
            )

    def quality_report(self, project_id: str) -> JobQualityReport:
        with self.connect() as connection:
            project = connection.execute(
                "SELECT total_frames, timing_status FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise ValueError("Project was not found")
            counts = connection.execute(
                """
                SELECT COUNT(*) AS frame_count,
                       SUM(CASE WHEN status='empty' THEN 1 ELSE 0 END) AS empty_count,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count
                FROM frames WHERE project_id=?
                """,
                (project_id,),
            ).fetchone()
            cue_rows = connection.execute(
                """
                SELECT start_ms, end_ms, text FROM cues
                WHERE project_id=? ORDER BY start_ms, frame_index
                """,
                (project_id,),
            ).fetchall()
        overlaps = sum(
            1
            for previous, current in zip(cue_rows, cue_rows[1:], strict=False)
            if int(current["start_ms"]) < int(previous["end_ms"])
        )
        suspicious_short = sum(
            1 for row in cue_rows if int(row["end_ms"]) - int(row["start_ms"]) < 250
        )
        suspicious_long = sum(
            1 for row in cue_rows if int(row["end_ms"]) - int(row["start_ms"]) > 10_000
        )
        adjacent_duplicates = sum(
            1
            for previous, current in zip(cue_rows, cue_rows[1:], strict=False)
            if str(previous["text"]).strip() == str(current["text"]).strip()
            and 0 <= int(current["start_ms"]) - int(previous["end_ms"]) <= 250
        )
        total_frames = max(int(project["total_frames"]), int(counts["frame_count"] or 0))
        recognized_lines = sum(
            max(1, len([line for line in str(row["text"]).splitlines() if line.strip()]))
            for row in cue_rows
        )
        return JobQualityReport(
            project_id=project_id,
            total_frames=total_frames,
            recognized_frames=len(cue_rows),
            recognized_lines=recognized_lines,
            empty_frames=int(counts["empty_count"] or 0),
            failed_frames=int(counts["failed_count"] or 0),
            overlaps=overlaps,
            suspicious_short=suspicious_short,
            suspicious_long=suspicious_long,
            adjacent_duplicates=adjacent_duplicates,
            first_start_ms=int(cue_rows[0]["start_ms"]) if cue_rows else None,
            last_end_ms=int(cue_rows[-1]["end_ms"]) if cue_rows else None,
            timing_status=str(project["timing_status"]),
        )

    def latest_project_id(self) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM projects ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return str(row["id"]) if row is not None else None

    def project_output_path(self, project_id: str) -> Path:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT output_path FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Project was not found")
        return Path(str(row["output_path"]))

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

    def enqueue(self, request: JobRequest) -> int:
        """Persist a job before it starts so application shutdown cannot lose it."""
        with self.connect() as connection:
            position = int(
                connection.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM job_queue"
                ).fetchone()[0]
            )
            cursor = connection.execute(
                """
                INSERT INTO job_queue(
                    input_path, output_path, stream_ordinal, stream_source_index,
                    stream_codec, stream_language, stream_title, engine, base_url,
                    model, export_format, status, position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(request.input_path.resolve()),
                    str(request.output_path.resolve()),
                    request.stream.ordinal,
                    request.stream.source_index,
                    request.stream.codec,
                    request.stream.language,
                    request.stream.title,
                    request.engine.value,
                    request.base_url,
                    request.model,
                    request.export_format,
                    QueueStatus.QUEUED.value,
                    position,
                ),
            )
            return int(cursor.lastrowid)

    def queue_jobs(self, *, active_only: bool = False) -> list[QueuedJob]:
        where = (
            "WHERE status IN ('queued', 'running', 'failed', 'cancelled')"
            if active_only
            else ""
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM job_queue {where} ORDER BY position, id"
            ).fetchall()
        jobs: list[QueuedJob] = []
        for row in rows:
            try:
                engine = EngineKind(str(row["engine"]))
                status = QueueStatus(str(row["status"]))
            except ValueError:
                continue
            jobs.append(
                QueuedJob(
                    id=int(row["id"]),
                    request=JobRequest(
                        input_path=Path(str(row["input_path"])),
                        output_path=Path(str(row["output_path"])),
                        stream=StreamInfo(
                            ordinal=int(row["stream_ordinal"]),
                            source_index=int(row["stream_source_index"]),
                            codec=str(row["stream_codec"]),
                            language=str(row["stream_language"]),
                            title=str(row["stream_title"]),
                        ),
                        engine=engine,
                        base_url=str(row["base_url"]),
                        model=str(row["model"]),
                        export_format=str(row["export_format"]),
                    ),
                    status=status,
                    position=int(row["position"]),
                    project_id=str(row["project_id"]),
                    last_error=str(row["last_error"]),
                )
            )
        return jobs

    def set_queue_status(
        self,
        queue_id: int,
        status: QueueStatus,
        *,
        project_id: str = "",
        error: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE job_queue
                SET status=?, project_id=CASE WHEN ?='' THEN project_id ELSE ? END,
                    last_error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status.value, project_id, project_id, error, queue_id),
            )

    def remove_queue_job(self, queue_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM job_queue WHERE id=? AND status != 'running'", (queue_id,)
            )

    def clear_finished_queue_jobs(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM job_queue WHERE status IN ('completed', 'cancelled')"
            )
