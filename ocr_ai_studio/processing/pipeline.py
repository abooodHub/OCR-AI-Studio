from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ocr_ai_studio.ai.vision_client import OCRRequestError, VisionClient
from ocr_ai_studio.domain.models import JobRequest, JobResult, JobStatus, SubtitleCue
from ocr_ai_studio.exporters.writers import SubtitleExporter
from ocr_ai_studio.media.bitmap_parser import MediaEngine
from ocr_ai_studio.media.ffmpeg import FFmpegService
from ocr_ai_studio.persistence.database import ProjectDatabase
from ocr_ai_studio.processing.image_processor import ImageProcessor


@dataclass(slots=True)
class JobCallbacks:
    status: Callable[[str], None] = lambda _message: None
    progress: Callable[[int, int], None] = lambda _current, _total: None
    log: Callable[[str, str], None] = lambda _level, _message: None
    cue: Callable[[SubtitleCue], None] = lambda _cue: None


class JobController:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._running = threading.Event()
        self._running.set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._running.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def wait_if_paused(self) -> None:
        while not self._running.wait(timeout=0.2):
            if self.cancelled:
                return


class ProcessingPipeline:
    def __init__(
        self,
        database: ProjectDatabase | None = None,
        media: FFmpegService | None = None,
        exporter: SubtitleExporter | None = None,
    ) -> None:
        self.database = database or ProjectDatabase()
        self.media = media or FFmpegService()
        self.exporter = exporter or SubtitleExporter()

    def run(
        self,
        request: JobRequest,
        client: VisionClient,
        controller: JobController,
        callbacks: JobCallbacks | None = None,
    ) -> JobResult:
        callbacks = callbacks or JobCallbacks()
        project_id = self.database.create_or_resume(request)
        try:
            if request.stream.is_text:
                self._extract_text(request, project_id, callbacks)
                return JobResult(
                    project_id=project_id,
                    status=JobStatus.COMPLETED,
                    total_frames=1,
                    completed_frames=1,
                    output_path=request.output_path,
                    message="Text subtitle stream was exported successfully",
                )
            return self._process_bitmap(request, project_id, client, controller, callbacks)
        except Exception as exc:
            self.database.set_status(project_id, JobStatus.FAILED, str(exc))
            callbacks.log("ERROR", str(exc))
            raise

    def _extract_text(self, request: JobRequest, project_id: str, callbacks: JobCallbacks) -> None:
        callbacks.status("Extracting the text subtitle stream")
        self.media.extract_text(
            request.input_path,
            request.stream.ordinal,
            request.output_path,
            request.export_format,
        )
        self.database.set_status(project_id, JobStatus.COMPLETED)
        callbacks.progress(1, 1)
        callbacks.log("INFO", f"Subtitle exported to {request.output_path}")

    def _process_bitmap(
        self,
        request: JobRequest,
        project_id: str,
        client: VisionClient,
        controller: JobController,
        callbacks: JobCallbacks,
    ) -> JobResult:
        image_processor = ImageProcessor()
        legacy_media = MediaEngine()
        completed = self.database.completed_frame_indexes(project_id)
        processed_count = 0
        failed_count = 0
        seen_count = 0

        with tempfile.TemporaryDirectory(prefix="ocr-ai-") as temp_directory:
            suffix = request.input_path.suffix.lower()
            is_vobsub = suffix in {".sub", ".idx"}
            embedded_vobsub = "dvd_subtitle" in request.stream.codec.lower() and not is_vobsub
            if embedded_vobsub:
                raise ValueError(
                    "Embedded DVD/VobSub tracks inside MKV or MKS are not safely supported yet; "
                    "extract the track as IDX/SUB first"
                )
            if is_vobsub:
                sub_path = request.input_path.with_suffix(".sub")
                if not sub_path.exists():
                    raise FileNotFoundError(f"Companion VobSub file was not found: {sub_path}")
                callbacks.status("Reading VobSub DVD subtitle images")
                sub_type, frame_source = legacy_media.parse_sub(str(sub_path))
                if sub_type != "vobsub":
                    raise ValueError(f"Expected VobSub bitmap subtitles, found {sub_type}")
            else:
                callbacks.status("Extracting the PGS subtitle stream")
                if suffix == ".sup":
                    sup_path = request.input_path
                else:
                    sup_path = Path(temp_directory) / "subtitle.sup"
                    self.media.extract_pgs(request.input_path, request.stream.ordinal, sup_path)
                callbacks.status("Reading PGS subtitle images")
                frame_source = legacy_media.parse_sup(str(sup_path))

            for frame_index, (start_ms, end_ms, image) in enumerate(frame_source):
                seen_count += 1
                if controller.cancelled:
                    self.database.set_totals(project_id, seen_count)
                    self.database.set_status(project_id, JobStatus.CANCELLED)
                    callbacks.status("Processing cancelled; progress was saved")
                    return JobResult(
                        project_id,
                        JobStatus.CANCELLED,
                        seen_count,
                        processed_count,
                        failed_count,
                        message="Processing was cancelled and progress was saved",
                    )

                controller.wait_if_paused()
                if controller.cancelled:
                    self.database.set_totals(project_id, seen_count)
                    self.database.set_status(project_id, JobStatus.CANCELLED)
                    callbacks.status("Processing cancelled; progress was saved")
                    return JobResult(
                        project_id,
                        JobStatus.CANCELLED,
                        seen_count,
                        processed_count,
                        failed_count,
                        message="Processing was cancelled and progress was saved",
                    )

                if frame_index in completed:
                    processed_count += 1
                    callbacks.progress(processed_count, 0)
                    continue

                prepared = (
                    image_processor.prepare_vobsub(image) if is_vobsub else image_processor.prepare(image)
                )
                if prepared is None:
                    self.database.record_frame(project_id, frame_index, start_ms, end_ms, status="empty")
                    processed_count += 1
                    callbacks.progress(processed_count, 0)
                    continue

                try:
                    text = client.ocr(prepared)
                except OCRRequestError as exc:
                    failed_count += 1
                    self.database.record_frame(
                        project_id,
                        frame_index,
                        start_ms,
                        end_ms,
                        status="failed",
                        error=str(exc),
                    )
                    callbacks.log("ERROR", f"Frame {frame_index + 1}: {exc}")
                    callbacks.progress(processed_count, 0)
                    continue

                if text:
                    cue = SubtitleCue(
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                        frame_index=frame_index,
                    )
                    self.database.save_cue(project_id, cue)
                    callbacks.cue(cue)
                    frame_status = "done"
                else:
                    frame_status = "empty"

                self.database.record_frame(project_id, frame_index, start_ms, end_ms, status=frame_status)
                processed_count += 1
                callbacks.progress(processed_count, 0)

        if seen_count == 0:
            raise ValueError("No bitmap subtitle frames were found; no empty output file was written")

        total = seen_count
        self.database.set_totals(project_id, total)
        cues = self.database.load_cues(project_id)
        if failed_count:
            self.database.set_status(
                project_id,
                JobStatus.NEEDS_REVIEW,
                f"{failed_count} frame(s) need to be retried",
            )
            callbacks.status(f"Finished with {failed_count} failed frame(s); review is required")
            callbacks.log("WARNING", "Partial results were saved, but final export was not marked complete")
            return JobResult(
                project_id,
                JobStatus.NEEDS_REVIEW,
                total,
                processed_count,
                failed_count,
                message=f"{failed_count} frame(s) need review",
            )

        if not cues:
            raise ValueError("No subtitle text was recognized; no empty output file was written")

        self.exporter.export(cues, request.output_path, request.export_format)
        self.database.set_status(project_id, JobStatus.COMPLETED)
        callbacks.progress(total, total)
        callbacks.status("Processing completed successfully")
        callbacks.log("INFO", f"Subtitle exported to {request.output_path}")
        return JobResult(
            project_id,
            JobStatus.COMPLETED,
            total,
            processed_count,
            failed_count,
            request.output_path,
            "Processing completed successfully",
        )
