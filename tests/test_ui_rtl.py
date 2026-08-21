import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea  # noqa: E402

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
        unsloth_index = window.engine_combo.findData(EngineKind.UNSLOTH.value)
        self.assertGreaterEqual(unsloth_index, 0)
        window.engine_combo.setCurrentIndex(unsloth_index)
        self.assertEqual(window.url_edit.text(), "http://127.0.0.1:8888/v1")
        self.assertFalse(window.api_key_edit.isHidden())
        self.assertTrue(window.model_combo.isEditable())
        self.assertEqual(window.refresh_models_button.text(), "تحديث الموديلات")
        window.close()
