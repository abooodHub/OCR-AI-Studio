from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from ocr_ai_studio.ai.vision_client import OCRRequestError
from ocr_ai_studio.domain.models import EngineKind, JobRequest, JobStatus, StreamInfo
from ocr_ai_studio.media.bitmap_parser import MediaEngine
from ocr_ai_studio.persistence.database import ProjectDatabase
from ocr_ai_studio.processing.image_processor import ImageProcessor
from ocr_ai_studio.processing.pipeline import JobController, ProcessingPipeline


class _UnusedVisionClient:
    def ocr(self, _image: bytes) -> str:
        raise AssertionError("OCR must not be called")


class _FailingVisionClient:
    def ocr(self, _image: bytes) -> str:
        raise OCRRequestError("temporary server failure")


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
            self.assertFalse(request.output_path.exists())
