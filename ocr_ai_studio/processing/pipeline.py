from __future__ import annotations

import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ocr_ai_studio.ai.vision_client import OCRConnectionError, OCRRequestError, VisionClient
from ocr_ai_studio.domain.models import JobRequest, JobResult, JobStatus, SubtitleCue
from ocr_ai_studio.exporters.writers import SubtitleExporter
from ocr_ai_studio.media.av_bitmap_parser import AVBitmapParser
from ocr_ai_studio.media.bdn_parser import BDNParser
from ocr_ai_studio.media.bitmap_parser import MediaEngine
from ocr_ai_studio.media.ffmpeg import FFmpegService
from ocr_ai_studio.persistence.database import ProjectDatabase
from ocr_ai_studio.processing.image_processor import ImageProcessor
from ocr_ai_studio.processing.timing_validator import SubtitleTimingValidator


@dataclass(slots=True)
class JobCallbacks:
    status: Callable[[str], None] = lambda _message: None
    progress: Callable[[int, int], None] = lambda _current, _total: None
    log: Callable[[str, str], None] = lambda _level, _message: None
    cue: Callable[[SubtitleCue], None] = lambda _cue: None
    preflight: Callable[[PreflightReport], bool] = lambda _report: True


@dataclass(frozen=True, slots=True)
class PreflightReport:
    sampled_frames: int
    recognized_frames: int
    failed_frames: int
    texts: tuple[str, ...]
    elapsed_seconds: float
    total_frames: int
    estimated_seconds: float


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
        self.database.set_timing_status(project_id, "source_native")
        self.database.set_totals(project_id, 1)
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
        timing_offset_ms = self.database.project_timing_offset(project_id)
        processed_count = 0
        failed_count = 0
        seen_count = 0
        total_hint = 0
        checkpoint_count = 0
        preflight_started: float | None = None
        preflight_samples: list[str] = []
        preflight_enabled = not completed

        def accept_preflight_sample(text: str) -> bool:
            if not preflight_enabled or len(preflight_samples) >= 3:
                return True
            preflight_samples.append(text.strip())
            if len(preflight_samples) < 3:
                return True
            elapsed = max(0.001, time.monotonic() - (preflight_started or time.monotonic()))
            recognized = sum(bool(value) for value in preflight_samples)
            estimate = elapsed * total_hint / len(preflight_samples) if total_hint else 0.0
            return callbacks.preflight(
                PreflightReport(
                    sampled_frames=len(preflight_samples),
                    recognized_frames=recognized,
                    failed_frames=len(preflight_samples) - recognized,
                    texts=tuple(value for value in preflight_samples if value),
                    elapsed_seconds=elapsed,
                    total_frames=total_hint,
                    estimated_seconds=estimate,
                )
            )

        with tempfile.TemporaryDirectory(prefix="ocr-ai-") as temp_directory:
            suffix = request.input_path.suffix.lower()
            standalone_vobsub = suffix in {".sub", ".idx"}
            is_vobsub = "dvd_subtitle" in request.stream.codec.lower() or standalone_vobsub
            is_bdn = request.stream.codec.lower() == "bdn_xml"
            is_pgs = any(
                token in request.stream.codec.lower() for token in ("pgs", "hdmv_pgs_subtitle")
            )
            is_generic_av_bitmap = any(
                token in request.stream.codec.lower() for token in ("dvb_subtitle", "xsub")
            )
            is_dvd_folder = request.input_path.is_dir()
            if is_dvd_folder:
                callbacks.status("Reading the DVD title and preserving its navigation timing")
                dvd_container = Path(temp_directory) / "dvd-title.mkv"
                title_match = re.search(r"DVD Title\s+(\d+)", request.stream.title, re.IGNORECASE)
                dvd_title = int(title_match.group(1)) if title_match else 1
                self.media.extract_dvd_title(
                    request.input_path,
                    request.stream.ordinal,
                    dvd_container,
                    title=dvd_title,
                )
                total_hint = AVBitmapParser().count(dvd_container, 0)
                self.database.set_totals(project_id, total_hint)
                self.database.set_timing_status(project_id, "validated")
                frame_source = AVBitmapParser().parse(dvd_container, 0)
            elif is_generic_av_bitmap:
                callbacks.status(f"Decoding {request.stream.codec} bitmap subtitle frames")
                total_hint = AVBitmapParser().count(request.input_path, request.stream.ordinal)
                self.database.set_totals(project_id, total_hint)
                self.database.set_timing_status(project_id, "source_native")
                frame_source = AVBitmapParser().parse(request.input_path, request.stream.ordinal)
            elif is_bdn:
                callbacks.status("Reading Blu-ray BDN subtitle images")
                bdn = BDNParser(request.input_path)
                total_hint = bdn.count_events()
                self.database.set_totals(project_id, total_hint)
                self.database.set_timing_status(project_id, "source_native")
                frame_source = bdn.parse()
            elif is_vobsub:
                if standalone_vobsub:
                    sub_path = request.input_path.with_suffix(".sub")
                    self.database.set_timing_status(project_id, "source_native")
                else:
                    callbacks.status("Extracting the embedded VobSub subtitle stream")
                    idx_path = Path(temp_directory) / "subtitle.idx"
                    self.media.extract_vobsub(request.input_path, request.stream.ordinal, idx_path)
                    sub_path = idx_path.with_suffix(".sub")
                    self.database.set_timing_status(project_id, "validated")
                if not sub_path.exists():
                    raise FileNotFoundError(f"Companion VobSub file was not found: {sub_path}")
                callbacks.status("Reading VobSub DVD subtitle images")
                total_hint = self.media.count_vobsub_frames(sub_path)
                if total_hint:
                    self.database.set_totals(project_id, total_hint)
                sub_type, frame_source = legacy_media.parse_sub(str(sub_path))
                if sub_type != "vobsub":
                    raise ValueError(f"Expected VobSub bitmap subtitles, found {sub_type}")
            elif is_pgs:
                callbacks.status("Extracting the PGS subtitle stream")
                if suffix in {".sup", ".pgs"}:
                    sup_path = request.input_path
                    self.database.set_timing_status(project_id, "source_native")
                else:
                    sup_path = Path(temp_directory) / "subtitle.sup"
                    self.media.extract_pgs(request.input_path, request.stream.ordinal, sup_path)
                    self.database.set_timing_status(project_id, "validated")
                callbacks.status("Reading PGS subtitle images")
                total_hint = legacy_media.count_sup_frames(str(sup_path))
                if total_hint:
                    self.database.set_totals(project_id, total_hint)
                frame_source = legacy_media.parse_sup(str(sup_path))
            else:
                raise ValueError(
                    f"تم اكتشاف ترجمة صورية من نوع {request.stream.codec}، "
                    "لكن محلل الصور الآمن لهذا النوع غير متوفر بعد. "
                    "أُوقفت المهمة بدل محاولة استخراجها بتنسيق خاطئ."
                )

            for frame_index, (start_ms, end_ms, image) in enumerate(frame_source):
                seen_count += 1
                start_ms += timing_offset_ms
                end_ms += timing_offset_ms
                if controller.cancelled:
                    partial_output = self._write_checkpoint(project_id, request, callbacks)
                    self.database.set_totals(project_id, total_hint or seen_count)
                    self.database.set_status(project_id, JobStatus.CANCELLED)
                    callbacks.status("Processing cancelled; progress was saved")
                    return JobResult(
                        project_id,
                        JobStatus.CANCELLED,
                        seen_count,
                        processed_count,
                        failed_count,
                        output_path=partial_output,
                        message="Processing was cancelled and progress was saved",
                    )

                controller.wait_if_paused()
                if controller.cancelled:
                    partial_output = self._write_checkpoint(project_id, request, callbacks)
                    self.database.set_totals(project_id, total_hint or seen_count)
                    self.database.set_status(project_id, JobStatus.CANCELLED)
                    callbacks.status("Processing cancelled; progress was saved")
                    return JobResult(
                        project_id,
                        JobStatus.CANCELLED,
                        seen_count,
                        processed_count,
                        failed_count,
                        output_path=partial_output,
                        message="Processing was cancelled and progress was saved",
                    )

                if frame_index in completed:
                    processed_count += 1
                    callbacks.progress(processed_count + failed_count, total_hint)
                    continue

                if preflight_enabled and preflight_started is None:
                    preflight_started = time.monotonic()

                variants = image_processor.prepared_variants(image, vobsub=is_vobsub)
                if not variants:
                    self.database.record_frame(project_id, frame_index, start_ms, end_ms, status="empty")
                    processed_count += 1
                    callbacks.progress(processed_count, total_hint)
                    if not accept_preflight_sample(""):
                        return self._preflight_cancelled(
                            project_id,
                            request,
                            seen_count,
                            processed_count,
                            failed_count,
                            callbacks,
                        )
                    continue

                text = ""
                last_error = "Vision model returned EMPTY for a non-blank subtitle image"
                attempts = 0
                for attempts, prepared in enumerate(variants, start=1):
                    try:
                        text = client.ocr(prepared)
                    except OCRConnectionError:
                        self._write_checkpoint(project_id, request, callbacks)
                        callbacks.log(
                            "WARNING",
                            "Vision server connection was lost; progress is saved for automatic resume",
                        )
                        raise
                    except OCRRequestError as exc:
                        last_error = str(exc)
                    if text:
                        if attempts > 1:
                            callbacks.log(
                                "INFO",
                                f"Frame {frame_index + 1}: OCR succeeded with image retry {attempts}",
                            )
                        break

                if not text:
                    failed_count += 1
                    self.database.record_frame(
                        project_id,
                        frame_index,
                        start_ms,
                        end_ms,
                        status="failed",
                        error=last_error,
                        image_jpeg=variants[0],
                        attempts=attempts,
                    )
                    callbacks.log(
                        "ERROR",
                        f"Frame {frame_index + 1}: failed after {attempts} image variants — {last_error}",
                    )
                    callbacks.progress(processed_count + failed_count, total_hint)
                    if not accept_preflight_sample(""):
                        return self._preflight_cancelled(
                            project_id,
                            request,
                            seen_count,
                            processed_count,
                            failed_count,
                            callbacks,
                        )
                    continue

                cue = SubtitleCue(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    frame_index=frame_index,
                )
                self.database.save_cue(project_id, cue)
                callbacks.cue(cue)

                self.database.record_frame(
                    project_id,
                    frame_index,
                    start_ms,
                    end_ms,
                    status="done",
                    image_jpeg=variants[0],
                    attempts=attempts,
                )
                processed_count += 1
                checkpoint_count += 1
                callbacks.progress(processed_count, total_hint)
                if checkpoint_count >= 25:
                    self._write_checkpoint(project_id, request, callbacks, announce=False)
                    checkpoint_count = 0
                if not accept_preflight_sample(text):
                    return self._preflight_cancelled(
                        project_id,
                        request,
                        seen_count,
                        processed_count,
                        failed_count,
                        callbacks,
                    )

        if seen_count == 0:
            raise ValueError("No bitmap subtitle frames were found; no empty output file was written")
        if total_hint and seen_count != total_hint:
            callbacks.log(
                "WARNING",
                "Bitmap event estimate differed from renderable frames "
                f"({total_hint} estimated, {seen_count} rendered); using rendered frame count",
            )
            total_hint = seen_count

        total = seen_count
        self.database.set_totals(project_id, total)
        cues = self.database.load_cues(project_id)
        quality = self.database.quality_report(project_id)
        timing = SubtitleTimingValidator().validate(
            cues,
            timing_status=quality.timing_status,
        )
        callbacks.log("INFO" if timing.valid else "ERROR", timing.summary)
        if timing.valid:
            callbacks.log(
                "INFO",
                f"Timing check: {timing.cue_count} cues, "
                f"first={timing.first_start_ms}, last={timing.last_end_ms}",
            )
        if failed_count:
            partial_output = self._partial_output_path(request.output_path)
            if cues:
                self.exporter.export(cues, partial_output, request.export_format)
                callbacks.log("INFO", f"Recoverable partial subtitle exported to {partial_output}")
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
                output_path=partial_output if cues else None,
                message=(
                    f"{failed_count} frame(s) need retry; completed text was saved to {partial_output}"
                    if cues
                    else f"{failed_count} frame(s) need retry"
                ),
                quality=quality,
            )

        if not cues:
            raise ValueError("No subtitle text was recognized; no empty output file was written")

        if not timing.valid:
            partial_output = self._write_checkpoint(project_id, request, callbacks)
            self.database.set_status(project_id, JobStatus.NEEDS_REVIEW, timing.summary)
            callbacks.status("Timing validation failed; final export was blocked")
            return JobResult(
                project_id,
                JobStatus.NEEDS_REVIEW,
                total,
                processed_count,
                failed_count,
                output_path=partial_output,
                message=timing.summary,
                quality=quality,
            )

        self.exporter.export(cues, request.output_path, request.export_format)
        partial_output = self._partial_output_path(request.output_path)
        if partial_output.exists():
            partial_output.unlink()
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
            quality,
        )

    @staticmethod
    def _partial_output_path(output_path: Path) -> Path:
        return output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")

    def _write_checkpoint(
        self,
        project_id: str,
        request: JobRequest,
        callbacks: JobCallbacks,
        *,
        announce: bool = True,
    ) -> Path | None:
        cues = self.database.load_cues(project_id)
        if not cues:
            return None
        partial_output = self._partial_output_path(request.output_path)
        self.exporter.export(cues, partial_output, request.export_format)
        if announce:
            callbacks.log("INFO", f"Progress checkpoint saved to {partial_output}")
        return partial_output

    def _preflight_cancelled(
        self,
        project_id: str,
        request: JobRequest,
        seen_count: int,
        processed_count: int,
        failed_count: int,
        callbacks: JobCallbacks,
    ) -> JobResult:
        partial_output = self._write_checkpoint(project_id, request, callbacks)
        self.database.set_totals(project_id, seen_count)
        self.database.set_status(project_id, JobStatus.CANCELLED)
        callbacks.status("Processing stopped after the sample check; progress was saved")
        return JobResult(
            project_id,
            JobStatus.CANCELLED,
            seen_count,
            processed_count,
            failed_count,
            output_path=partial_output,
            message="Processing stopped after the sample check",
        )
