import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QWidget  # noqa: E402

from ocr_ai_studio.domain.models import EngineKind  # noqa: E402
from ocr_ai_studio.ui.main_window import MainWindow  # noqa: E402
from ocr_ai_studio.ui.theme import APP_STYLE  # noqa: E402


class RightToLeftInterfaceTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        cls.application.setStyle("Fusion")
        cls.application.setStyleSheet(APP_STYLE)

    def test_sidebar_is_rendered_on_the_right(self) -> None:
        window = MainWindow()
        window.resize(1280, 840)
        window.show()
        self.application.processEvents()
        sidebar = window.findChild(QFrame, "sidebar")
        self.assertIsNotNone(sidebar)
        self.assertGreater(sidebar.x(), window.width() // 2)
        self.assertEqual(window.layoutDirection(), Qt.LayoutDirection.RightToLeft)
        window.close()

    def test_main_navigation_contains_three_pages(self) -> None:
        window = MainWindow()
        self.assertEqual(window.pages.count(), 3)
        self.assertEqual(len(window.nav_buttons), 3)
        window.close()

    def test_pages_are_scrollable_and_unsloth_is_available(self) -> None:
        window = MainWindow()
        self.assertGreaterEqual(len(window.findChildren(QScrollArea)), 3)
        page_titles = window.findChildren(QLabel, "pageTitle")
        self.assertGreaterEqual(len(page_titles), 2)
        self.assertTrue(
            all(title.alignment() & Qt.AlignmentFlag.AlignRight for title in page_titles)
        )
        unsloth_index = window.engine_combo.findData(EngineKind.UNSLOTH.value)
        self.assertGreaterEqual(unsloth_index, 0)
        window.engine_combo.setCurrentIndex(unsloth_index)
        self.assertEqual(window.url_edit.text(), "http://127.0.0.1:8888/v1")
        self.assertFalse(window.api_key_edit.isHidden())
        self.assertTrue(window.model_combo.isEditable())
        self.assertEqual(window.refresh_models_button.text(), "تحديث الموديلات")
        window.close()

    def test_ai_page_heading_block_is_physically_pinned_to_the_right(self) -> None:
        window = MainWindow()
        window.resize(1280, 840)
        window.pages.setCurrentIndex(1)
        window.show()
        self.application.processEvents()
        blocks = [
            block
            for block in window.findChildren(QWidget, "pageHeaderText")
            if block.isVisible()
        ]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertGreater(block.x(), 0)
        self.assertEqual(block.x() + block.width(), block.parentWidget().width())
        window.close()

    def test_provider_cards_use_icons_without_port_badges(self) -> None:
        window = MainWindow()
        provider_cards = window.findChildren(QFrame, "providerCard")

        self.assertEqual(len(provider_cards), 3)
        for card in provider_cards:
            self.assertEqual(card.findChildren(QLabel, "portBadge"), [])
            icons = card.findChildren(QLabel, "providerIcon")
            self.assertEqual(len(icons), 1)
            self.assertFalse(icons[0].pixmap().isNull())
        window.close()

    def test_provider_cards_select_the_engine_and_runtime_controls_exist(self) -> None:
        window = MainWindow()

        window.provider_cards[EngineKind.OLLAMA.value].selected.emit(EngineKind.OLLAMA.value)

        self.assertEqual(window.engine_combo.currentData(), EngineKind.OLLAMA.value)
        self.assertTrue(window.provider_cards[EngineKind.OLLAMA.value].property("selected"))
        self.assertEqual(window.url_edit.text(), "http://127.0.0.1:11434/v1")
        self.assertEqual(window.runtime_start_button.text(), "تشغيل المحرك")
        self.assertIn("تلقائي", window.auto_start_engine_check.text())
        window.close()

    def test_sidebar_shows_selected_engine_model_and_connection_state(self) -> None:
        window = MainWindow()
        lm_studio_index = window.engine_combo.findData(EngineKind.LM_STUDIO.value)
        window.engine_combo.setCurrentIndex(lm_studio_index)
        window.model_combo.setEditText("qwen2.5-vl-7b")

        window._set_engine_card("success", "Vision جاهز")

        self.assertIn("LM Studio", window.engine_status_title.text())
        self.assertIn("متصل", window.engine_status_title.text())
        self.assertEqual(window.engine_status_model.text(), "qwen2.5-vl-7b")
        self.assertEqual(window.engine_status_detail.text(), "Vision جاهز")
        self.assertEqual(window.engine_status_card.property("tone"), "success")
        window.close()

    def test_progress_shows_count_percentage_and_readable_time(self) -> None:
        window = MainWindow()
        window._job_started_at = None

        window._update_progress(100, 400)

        self.assertEqual(window.progress.value(), 100)
        self.assertEqual(window.progress.maximum(), 400)
        self.assertEqual(window.progress.format(), "25%")
        self.assertIn("100 / 400", window.progress_meta.text())
        self.assertNotIn("ms", window.progress_meta.text())
        window.close()

    def test_project_step_selection_starts_a_visual_transition(self) -> None:
        window = MainWindow()

        window._set_project_step(1)

        self.assertEqual(window.project_steps[0].objectName(), "stepDone")
        self.assertEqual(window.project_steps[1].objectName(), "stepActive")
        self.assertEqual(window.project_steps[2].objectName(), "step")
        self.assertIn(0, window._step_animations)
        self.assertIn(1, window._step_animations)
        window.close()
