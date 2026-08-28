import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea  # noqa: E402

from ocr_ai_studio.ai.model_catalog import ModelInfo  # noqa: E402
from ocr_ai_studio.domain.models import EngineKind, JobRequest, StreamInfo  # noqa: E402
from ocr_ai_studio.processing.pipeline import PreflightReport  # noqa: E402
from ocr_ai_studio.ui.main_window import MainWindow  # noqa: E402
from ocr_ai_studio.ui.theme import APP_STYLE  # noqa: E402


class RightToLeftInterfaceTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        cls.application.setStyle("Fusion")
        cls.application.setStyleSheet(APP_STYLE)

    def test_interface_is_one_rtl_page_without_sidebar(self) -> None:
        window = MainWindow()
        self.assertEqual(window.layoutDirection(), Qt.LayoutDirection.RightToLeft)
        self.assertEqual(len(window.findChildren(QScrollArea)), 1)
        self.assertIsNone(window.findChild(QFrame, "sidebar"))
        self.assertFalse(hasattr(window, "pages"))
        self.assertFalse(hasattr(window, "provider_cards"))
        window.close()

    def test_all_supported_engines_are_available(self) -> None:
        window = MainWindow()
        for engine in EngineKind:
            self.assertGreaterEqual(window.engine_combo.findData(engine.value), 0)
        window.close()

    def test_model_catalog_is_compact_and_prefers_vision(self) -> None:
        window = MainWindow()
        window._busy_token = 4
        models = [
            ModelInfo("vision-model", EngineKind.LM_STUDIO, supports_vision=True),
            ModelInfo("unknown-model", EngineKind.LM_STUDIO, supports_vision=None),
            ModelInfo("text-model", EngineKind.LM_STUDIO, supports_vision=False),
        ]
        window._models_loaded((4, models))
        self.assertEqual(window.model_combo.count(), 2)
        self.assertEqual(window._current_model_id(), "vision-model")
        self.assertIn("2", window.connection_status.text())
        self.assertFalse(hasattr(window, "model_list"))
        window.close()

    def test_progress_keeps_frames_lines_and_readable_time(self) -> None:
        window = MainWindow()
        window._recognized_lines = 37
        window._job_started_at = None
        window._update_progress(100, 400)
        text = window.progress_meta.text()
        self.assertEqual(window.progress.format(), "25%")
        self.assertIn("100 / 400", text)
        self.assertIn("الأسطر 37", text)
        self.assertIn("00:00", text)
        self.assertNotIn("ms", text)
        window.close()

    def test_log_is_collapsed_until_requested(self) -> None:
        window = MainWindow()
        self.assertTrue(window.log_view.isHidden())
        window._toggle_log()
        self.assertFalse(window.log_view.isHidden())
        self.assertEqual(window.log_toggle.text(), "إخفاء التفاصيل")
        window.close()

    def test_queue_is_hidden_for_a_single_job(self) -> None:
        window = MainWindow()
        window._refresh_queue()
        self.assertTrue(window.queue_card.isHidden())
        window.close()

    def test_queue_appears_for_multiple_jobs(self) -> None:
        window = MainWindow()
        stream = StreamInfo(0, 0, "hdmv_pgs_subtitle")
        for number in (1, 2):
            request = JobRequest(
                Path(f"movie-{number}.mkv"),
                Path(f"movie-{number}.srt"),
                stream,
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            window._database().enqueue(request)
        window._refresh_queue()
        self.assertFalse(window.queue_card.isHidden())
        self.assertEqual(window.queue_table.rowCount(), 2)
        window.close()

    def test_output_name_does_not_overwrite_existing_or_reserved_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "movie.ocr.srt"
            candidate.touch()
            second = candidate.with_name("movie.ocr-2.srt")
            result = MainWindow._unused_output_path(candidate, {second})
            self.assertEqual(result.name, "movie.ocr-3.srt")

    def test_dvd_source_asks_for_title_number(self) -> None:
        window = MainWindow()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_ts = root / "VIDEO_TS"
            video_ts.mkdir()
            (video_ts / "VIDEO_TS.IFO").touch()
            with (
                patch("ocr_ai_studio.ui.main_window.QInputDialog.getInt", return_value=(4, True)),
                patch.object(window, "_scan_streams") as scan,
            ):
                window._accept_source(root)
            self.assertEqual(window._dvd_title, 4)
            self.assertIn("title-4", window.output_edit.text())
            scan.assert_called_once()
        window.close()

    def test_failed_sample_stops_before_long_processing(self) -> None:
        window = MainWindow()
        worker = Mock()
        window.worker = worker
        report = PreflightReport(3, 0, 3, (), 4.0, 1_500, 2_000.0)
        with patch("ocr_ai_studio.ui.main_window.QMessageBox.warning"):
            window._show_preflight_result(report)
        worker.decide_preflight.assert_called_once_with(False)
        window.worker = None
        window.close()
