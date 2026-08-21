from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ocr_ai_studio.ui.main_window import MainWindow
from ocr_ai_studio.ui.theme import APP_STYLE


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    application = QApplication(sys.argv)
    application.setApplicationName("OCR-AI Studio")
    application.setOrganizationName("OCR-AI")
    icon_path = Path(__file__).resolve().parents[1] / "assets" / "ocr-ai-studio.png"
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    application.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return application.exec()
