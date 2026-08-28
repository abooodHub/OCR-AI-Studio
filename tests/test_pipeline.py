from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from ocr_ai_studio.ai.vision_client import OCRRequestError
from ocr_ai_studio.domain.models import EngineKind, JobRequest, JobStatus, StreamInfo
from ocr_ai_studio.media.av_bitmap_parser import AVBitmapParser
from ocr_ai_studio.media.bitmap_parser import MediaEngine
from ocr_ai_studio.persistence.database import ProjectDatabase
from ocr_ai_studio.processing.image_processor import ImageProcessor
from ocr_ai_studio.processing.pipeline import JobCallbacks, JobController, ProcessingPipeline


class _UnusedVisionClient:
    def ocr(self, _image: bytes) -> str:
        raise AssertionError("OCR must not be called")


class _FailingVisionClient:
    def ocr(self, _image: bytes) -> str:
        raise OCRRequestError("temporary server failure")


class _EmptyVisionClient:
    def ocr(self, _image: bytes) -> str:
        return ""


class _SuccessfulVisionClient:
    def ocr(self, _image: bytes) -> str:
        return "نص مستخرج"


class _CountingVisionClient:
    def __init__(self, checkpoint: Path | None = None) -> None:
        self.calls = 0
        self.checkpoint = checkpoint

    def ocr(self, _image: bytes) -> str:
        self.calls += 1
        if self.calls == 26 and self.checkpoint is not None:
            if not self.checkpoint.exists():
                raise AssertionError("Periodic subtitle checkpoint was not written")
        return f"نص {self.calls}"


class _SecondVariantVisionClient:
    def __init__(self) -> None:
        self.calls = 0

    def ocr(self, _image: bytes) -> str:
        self.calls += 1
        return "" if self.calls == 1 else "نجحت المحاولة البديلة"


class PipelineTests(TestCase):
    @staticmethod
    def _request(root: Path) -> JobRequest:
        source = root / "subtitle.sup"
        source.write_bytes(b"PG")
        return JobRequest(
            source,
            root / "output.srt",
            StreamInfo(0, 0, "hdmv_pgs_subtitle"),
            EngineKind.LM_STUDIO,
            "http://127.0.0.1:1234/v1",
            "vision-model",
        )

    def test_zero_bitmap_frames_fail_without_writing_empty_output(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            with patch.object(MediaEngine, "parse_sup", return_value=iter(())):
                with self.assertRaisesRegex(ValueError, "No bitmap subtitle frames"):
                    pipeline.run(request, _UnusedVisionClient(), JobController())
            project = next(iter(database.recent_projects()))
            self.assertEqual(project["status"], JobStatus.FAILED.value)
            self.assertFalse(request.output_path.exists())

    def test_failed_frame_returns_needs_review_instead_of_success(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGB", (100, 40), "white")
            with (
                patch.object(MediaEngine, "parse_sup", return_value=iter([(0, 1000, frame)])),
                patch.object(ImageProcessor, "prepare", return_value=b"prepared"),
            ):
                result = pipeline.run(request, _FailingVisionClient(), JobController())
            self.assertEqual(result.status, JobStatus.NEEDS_REVIEW)
            self.assertEqual(result.failed_frames, 1)
            self.assertIsNotNone(result.quality)
            self.assertEqual(result.quality.failed_frames, 1)
            self.assertEqual(database.review_frames(result.project_id)[0].image_jpeg, b"prepared")
            self.assertFalse(request.output_path.exists())

    def test_non_blank_frame_reported_empty_requires_review(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGB", (100, 40), "white")
            with (
                patch.object(MediaEngine, "parse_sup", return_value=iter([(0, 1000, frame)])),
                patch.object(ImageProcessor, "prepare", return_value=b"prepared"),
            ):
                result = pipeline.run(request, _EmptyVisionClient(), JobController())

            self.assertEqual(result.status, JobStatus.NEEDS_REVIEW)
            review = database.review_frames(result.project_id)
            self.assertEqual(review[0].status, "failed")
            self.assertIn("EMPTY", review[0].error)
            self.assertFalse(request.output_path.exists())

    def test_failed_frame_is_retried_with_an_alternate_image_before_failure(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGB", (100, 40), "white")
            client = _SecondVariantVisionClient()
            with (
                patch.object(MediaEngine, "parse_sup", return_value=iter([(0, 1_000, frame)])),
                patch.object(
                    ImageProcessor,
                    "prepared_variants",
                    return_value=[b"normal", b"contrast", b"binary"],
                ),
            ):
                result = pipeline.run(request, client, JobController())

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(client.calls, 2)
            self.assertEqual(database.load_cues(result.project_id)[0].text, "نجحت المحاولة البديلة")

    def test_embedded_vobsub_is_extracted_and_processed_with_validated_timing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"matroska")
            request = JobRequest(
                source,
                root / "output.srt",
                StreamInfo(0, 2, "dvd_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            database = ProjectDatabase(root / "projects.sqlite3")
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGBA", (100, 40), "white")

            def extract_vobsub(_source: Path, _ordinal: int, destination: Path) -> None:
                destination.write_text(
                    "timestamp: 00:00:01:000, filepos: 000000000\n", encoding="utf-8"
                )
                destination.with_suffix(".sub").write_bytes(b"\x00\x00\x01\xba")

            with (
                patch.object(pipeline.media, "extract_vobsub", side_effect=extract_vobsub),
                patch.object(pipeline.media, "count_vobsub_frames", return_value=1),
                patch.object(
                    MediaEngine, "parse_sub", return_value=("vobsub", iter([(1_000, 2_000, frame)]))
                ),
                patch.object(ImageProcessor, "prepare_vobsub", return_value=b"prepared"),
            ):
                result = pipeline.run(request, _SuccessfulVisionClient(), JobController())

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertTrue(request.output_path.exists())
            self.assertEqual(result.quality.timing_status, "validated")
            self.assertEqual(database.load_cues(result.project_id)[0].start_ms, 1_000)

    def test_bitmap_event_estimate_mismatch_uses_rendered_frame_count(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGBA", (100, 40), "white")
            with (
                patch.object(MediaEngine, "count_sup_frames", return_value=2),
                patch.object(MediaEngine, "parse_sup", return_value=iter([(0, 1_000, frame)])),
                patch.object(ImageProcessor, "prepare", return_value=b"prepared"),
            ):
                result = pipeline.run(request, _SuccessfulVisionClient(), JobController())

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertTrue(request.output_path.exists())
            project = next(iter(database.recent_projects()))
            self.assertEqual(project["status"], JobStatus.COMPLETED.value)
            self.assertEqual(project["total_frames"], 1)

    def test_failed_frames_still_export_a_recoverable_partial_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGB", (100, 40), "white")
            client = _SecondVariantVisionClient()
            client.calls = -1  # first two calls succeed/fail pattern leaves a later frame empty
            with (
                patch.object(
                    MediaEngine,
                    "parse_sup",
                    return_value=iter([(0, 1_000, frame), (2_000, 3_000, frame)]),
                ),
                patch.object(
                    ImageProcessor,
                    "prepared_variants",
                    side_effect=[[b"first"], [b"second"]],
                ),
            ):
                result = pipeline.run(request, client, JobController())

            self.assertEqual(result.status, JobStatus.NEEDS_REVIEW)
            self.assertIsNotNone(result.output_path)
            self.assertTrue(result.output_path.exists())
            self.assertIn("نجحت المحاولة البديلة", result.output_path.read_text(encoding="utf-8"))

    def test_dvb_bitmap_codec_is_decoded_through_av_without_changing_timing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "broadcast.ts"
            source.write_bytes(b"transport-stream")
            request = JobRequest(
                source,
                root / "output.srt",
                StreamInfo(0, 2, "dvb_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            database = ProjectDatabase(root / "projects.sqlite3")
            pipeline = ProcessingPipeline(database=database)

            frame = Image.new("RGBA", (100, 40), "white")
            with (
                patch.object(AVBitmapParser, "count", return_value=1),
                patch.object(AVBitmapParser, "parse", return_value=iter([(1_250, 2_500, frame)])),
                patch.object(ImageProcessor, "prepared_variants", return_value=[b"prepared"]),
            ):
                result = pipeline.run(request, _SuccessfulVisionClient(), JobController())

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(database.load_cues(result.project_id)[0].start_ms, 1_250)
            self.assertTrue(request.output_path.exists())

    def test_preflight_can_stop_a_long_job_after_three_frames(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGB", (100, 40), "white")
            frames = [(index * 1_000, index * 1_000 + 800, frame) for index in range(8)]
            client = _CountingVisionClient()
            reports = []
            callbacks = JobCallbacks(
                preflight=lambda report: reports.append(report) is None and False
            )
            with (
                patch.object(MediaEngine, "count_sup_frames", return_value=8),
                patch.object(MediaEngine, "parse_sup", return_value=iter(frames)),
                patch.object(ImageProcessor, "prepared_variants", return_value=[b"prepared"]),
            ):
                result = pipeline.run(request, client, JobController(), callbacks)

            self.assertEqual(result.status, JobStatus.CANCELLED)
            self.assertEqual(client.calls, 3)
            self.assertEqual(reports[0].recognized_frames, 3)
            self.assertEqual(reports[0].total_frames, 8)
            self.assertTrue(result.output_path.exists())
            self.assertFalse(request.output_path.exists())

    def test_partial_subtitle_is_updated_every_twenty_five_new_cues(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ProjectDatabase(root / "projects.sqlite3")
            request = self._request(root)
            checkpoint = root / "output.partial.srt"
            pipeline = ProcessingPipeline(database=database)
            frame = Image.new("RGB", (100, 40), "white")
            frames = [(index * 1_000, index * 1_000 + 800, frame) for index in range(26)]
            client = _CountingVisionClient(checkpoint)
            with (
                patch.object(MediaEngine, "count_sup_frames", return_value=26),
                patch.object(MediaEngine, "parse_sup", return_value=iter(frames)),
                patch.object(ImageProcessor, "prepared_variants", return_value=[b"prepared"]),
            ):
                result = pipeline.run(request, client, JobController())

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(client.calls, 26)
            self.assertTrue(request.output_path.exists())
            self.assertFalse(checkpoint.exists())
