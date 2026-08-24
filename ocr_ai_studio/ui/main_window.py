from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ocr_ai_studio import __version__
from ocr_ai_studio.ai.model_catalog import ModelCatalogClient, ModelInfo
from ocr_ai_studio.ai.runtime_manager import (
    EngineRuntimeError,
    EngineRuntimeManager,
    RuntimeSnapshot,
    RuntimeState,
)
from ocr_ai_studio.ai.vision_client import VisionClient
from ocr_ai_studio.config.settings import AppSettings, SettingsStore
from ocr_ai_studio.domain.models import EngineKind, JobRequest, JobResult, JobStatus, StreamInfo, SubtitleCue
from ocr_ai_studio.media.ffmpeg import FFmpegService
from ocr_ai_studio.processing.pipeline import JobCallbacks, JobController, ProcessingPipeline


class AsyncBridge(QObject):
    streams_ready = Signal(object)
    models_ready = Signal(object)
    models_failed = Signal(object)
    model_checked = Signal(object)
    model_check_failed = Signal(str)
    runtime_checked = Signal(object)
    runtime_action_completed = Signal(object)
    runtime_job_checked = Signal(object)
    runtime_error = Signal(object)
    error = Signal(str)


class ProcessingThread(QThread):
    status_changed = Signal(str)
    progress_changed = Signal(int, int)
    log_received = Signal(str, str)
    cue_received = Signal(object)
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, request: JobRequest, settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self.settings = settings
        self.controller = JobController()

    def run(self) -> None:
        callbacks = JobCallbacks(
            status=self.status_changed.emit,
            progress=self.progress_changed.emit,
            log=self.log_received.emit,
            cue=self.cue_received.emit,
        )
        try:
            client = VisionClient(
                EngineKind(self.settings.engine),
                self.settings.base_url,
                self.settings.model,
                self.settings.request_timeout_seconds,
                self.settings.max_retries,
                self.settings.api_key,
            )
            result = ProcessingPipeline().run(self.request, client, self.controller, callbacks)
            self.result_ready.emit(result)
        except Exception as exc:  # worker boundary
            self.failed.emit(str(exc))


class EngineStatusCard(QFrame):
    """Clickable summary of the selected inference engine and model."""

    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ProviderCard(QFrame):
    """Selectable provider card used as a visual engine switch."""

    selected = Signal(str)

    def __init__(self, engine: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selected.emit(self.engine)
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.media = FFmpegService()
        self.runtime_manager = EngineRuntimeManager()
        self.bridge = AsyncBridge(self)
        self.bridge.streams_ready.connect(self._show_streams)
        self.bridge.models_ready.connect(self._show_model_catalog)
        self.bridge.models_failed.connect(self._show_model_catalog_error)
        self.bridge.model_checked.connect(self._show_model_result)
        self.bridge.model_check_failed.connect(self._show_model_check_error)
        self.bridge.runtime_checked.connect(self._show_runtime_snapshot)
        self.bridge.runtime_action_completed.connect(self._runtime_action_finished)
        self.bridge.runtime_job_checked.connect(self._runtime_job_finished)
        self.bridge.runtime_error.connect(self._runtime_task_failed)
        self.bridge.error.connect(self._show_async_error)
        self.streams: list[StreamInfo] = []
        self.worker: ProcessingThread | None = None
        self._paused = False
        self._model_refresh_token = 0
        self._runtime_task_token = 0
        self._runtime_busy = False
        self._job_started_at: float | None = None
        self._step_animations: dict[int, QPropertyAnimation] = {}

        self.setWindowTitle(f"OCR-AI Studio {__version__}")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(900, 600)
        self.resize(1280, 840)
        self._build_ui()
        self._apply_settings_to_ui()
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(6_000)
        self.runtime_timer.timeout.connect(self._poll_runtime)
        self.runtime_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.setDirection(QBoxLayout.Direction.RightToLeft)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_project_page())
        self.pages.addWidget(self._build_ai_page())
        self.pages.addWidget(self._build_diagnostics_page())
        root_layout.addWidget(self.pages, 1)
        root_layout.addWidget(self._build_sidebar())
        self.setCentralWidget(root)
        self.statusBar().setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.statusBar().showMessage("جاهز — تحقق من وجهة الخادم قبل بدء المعالجة")

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(252)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 26, 20, 20)
        layout.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_icon = QLabel("AI", objectName="brandIcon")
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_text = QVBoxLayout()
        brand = QLabel("OCR-AI", objectName="brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignRight)
        edition = QLabel("استوديو الترجمة الذكي", objectName="brandSubtitle")
        edition.setAlignment(Qt.AlignmentFlag.AlignRight)
        brand_text.addWidget(brand)
        brand_text.addWidget(edition)
        brand_row.addLayout(brand_text, 1)
        brand_row.addWidget(brand_icon)
        layout.addLayout(brand_row)
        layout.addSpacing(28)
        nav_label = QLabel("مساحة العمل", objectName="navCaption")
        nav_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(nav_label)
        self.nav_buttons: list[QPushButton] = []
        pages = (("◈", "مشروع التحويل"), ("✦", "محرك الذكاء الاصطناعي"), ("✓", "فحص النظام"))
        for index, (icon, text) in enumerate(pages):
            button = QPushButton(f"{icon}   {text}", objectName="nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=index: self._select_page(page))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch(1)

        self.engine_status_card = EngineStatusCard()
        self.engine_status_card.setObjectName("engineStatusCard")
        self.engine_status_card.setProperty("tone", "pending")
        self.engine_status_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.engine_status_card.clicked.connect(lambda: self._select_page(1))
        engine_layout = QVBoxLayout(self.engine_status_card)
        engine_layout.setContentsMargins(14, 13, 14, 13)
        engine_layout.setSpacing(4)
        self.engine_status_title = QLabel("◌  محرك غير محدد", objectName="engineStatusTitle")
        self.engine_status_title.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.engine_status_model = QLabel("لم يتم اختيار موديل", objectName="engineStatusModel")
        self.engine_status_model.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.engine_status_model.setWordWrap(True)
        self.engine_status_detail = QLabel(
            "اضغط لإعداد محرك الذكاء الاصطناعي",
            objectName="engineStatusDetail",
        )
        self.engine_status_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.engine_status_detail.setWordWrap(True)
        engine_layout.addWidget(self.engine_status_title)
        engine_layout.addWidget(self.engine_status_model)
        engine_layout.addWidget(self.engine_status_detail)
        layout.addWidget(self.engine_status_card)
        return sidebar

    def _select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)
        if index == 1 and hasattr(self, "runtime_check_button"):
            QTimer.singleShot(0, self._check_runtime)

    @staticmethod
    def _engine_name(engine: str) -> str:
        return {
            EngineKind.LM_STUDIO.value: "LM Studio",
            EngineKind.OLLAMA.value: "Ollama",
            EngineKind.UNSLOTH.value: "Unsloth",
            EngineKind.CUSTOM.value: "خادم مخصص",
        }.get(engine, "محرك غير معروف")

    def _set_engine_card(self, tone: str, detail: str | None = None) -> None:
        if not hasattr(self, "engine_status_card"):
            return
        engine = (
            str(self.engine_combo.currentData())
            if hasattr(self, "engine_combo")
            else self.settings.engine
        )
        engine_name = self._engine_name(engine)
        model = self.model_edit.text().strip() if hasattr(self, "model_edit") else self.settings.model
        state = {
            "pending": "محدد",
            "working": "جارٍ الاتصال",
            "processing": "نشط",
            "success": "متصل",
            "warning": "يحتاج انتباه",
            "error": "غير متصل",
        }.get(tone, "محدد")
        marker = "●" if tone in {"success", "working", "processing", "warning", "error"} else "◌"
        defaults = {
            "pending": "اختبر الاتصال للتأكد من جاهزية الموديل",
            "working": "جاري التحقق من الخادم والموديل…",
            "processing": "يعالج صور الترجمة الآن",
            "success": "جاهز لاستقبال صور الترجمة",
            "warning": "الخادم متصل لكن الموديل يحتاج مراجعة",
            "error": "تعذر الوصول إلى الخادم المحدد",
        }
        self.engine_status_title.setText(f"{marker}  {engine_name} {state}")
        self.engine_status_model.setText(model or "لم يتم اختيار موديل")
        self.engine_status_detail.setText(detail or defaults.get(tone, defaults["pending"]))
        self.engine_status_card.setToolTip(self.url_edit.text().strip() if hasattr(self, "url_edit") else "")
        self.engine_status_card.setProperty("tone", tone)
        self.engine_status_card.style().unpolish(self.engine_status_card)
        self.engine_status_card.style().polish(self.engine_status_card)
        for label in (
            self.engine_status_title,
            self.engine_status_model,
            self.engine_status_detail,
        ):
            label.style().unpolish(label)
            label.style().polish(label)

    @staticmethod
    def _page_shell(title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)
        if title or description:
            header = QFrame(objectName="pageHeader")
            # Keep the outer row absolute LTR so the stretch pins the RTL text block to the right.
            header.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            header_row = QHBoxLayout(header)
            header_row.setDirection(QBoxLayout.Direction.LeftToRight)
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(0)
            text_block = QWidget(objectName="pageHeaderText")
            text_block.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            text_block.setMinimumWidth(520)
            text_block.setMaximumWidth(760)
            text_block.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            titles = QVBoxLayout(text_block)
            titles.setContentsMargins(0, 0, 0, 0)
            titles.setSpacing(4)
            if title:
                page_title = QLabel(title, objectName="pageTitle")
                page_title.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
                page_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                page_title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                titles.addWidget(page_title)
            if description:
                page_description = QLabel(description, objectName="pageDescription")
                page_description.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
                page_description.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Preferred,
                )
                page_description.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                page_description.setWordWrap(True)
                titles.addWidget(page_description)
            header_row.addStretch(1)
            header_row.addWidget(
                text_block,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            )
            layout.addWidget(header, 0, Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        page = QWidget()
        shell_layout = QVBoxLayout(page)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.addWidget(scroll)
        return page, layout

    @staticmethod
    def _card() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(objectName="card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        return card, layout

    @staticmethod
    def _section_title(title: str, description: str) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(3)
        heading = QLabel(title, objectName="sectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignRight)
        helper = QLabel(description, objectName="sectionDescription")
        helper.setAlignment(Qt.AlignmentFlag.AlignRight)
        helper.setWordWrap(True)
        block.addWidget(heading)
        block.addWidget(helper)
        return block

    def _build_project_page(self) -> QWidget:
        page, layout = self._page_shell("", "")
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.project_steps: list[QLabel] = []
        for number, label in (
            ("١", "اختيار المصدر"),
            ("٢", "تحديد المسار"),
            ("٣", "المعالجة والتصدير"),
        ):
            step = QLabel(f"{number}   {label}", objectName="step")
            step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step.setMinimumHeight(40)
            opacity = QGraphicsOpacityEffect(step)
            opacity.setOpacity(1.0)
            step.setGraphicsEffect(opacity)
            steps.addWidget(step)
            self.project_steps.append(step)
        layout.addLayout(steps)
        self._set_project_step(0, animate=False)

        file_card, file_layout = self._card()
        file_layout.addLayout(
            self._section_title(
                "ملف المصدر",
                "اختر حاوية MKV/MKS أو ملف ترجمة صورية مستقل من نوع SUP أو IDX/SUB.",
            )
        )
        file_row = QHBoxLayout()
        file_row.setSpacing(10)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("اختر ملف MKS أو MKV أو SUP أو IDX/SUB")
        self.input_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.input_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.input_edit.setClearButtonEnabled(True)
        self.input_edit.textChanged.connect(self.input_edit.setToolTip)
        browse = QPushButton("اختيار ملف", objectName="secondary")
        browse.clicked.connect(self._browse_input)
        scan = QPushButton("تحليل المسارات", objectName="primary")
        scan.clicked.connect(self._scan_streams)
        file_row.addWidget(self.input_edit, 1)
        file_row.addWidget(browse)
        file_row.addWidget(scan)
        file_layout.addLayout(file_row)
        layout.addWidget(file_card)

        stream_card, stream_layout = self._card()
        stream_layout.addLayout(
            self._section_title(
                "مسارات الترجمة المتاحة",
                "اختر المسار المطلوب؛ المسارات النصية تُستخرج مباشرة والصورية تُرسل إلى موديل Vision.",
            )
        )
        self.stream_table = QTableWidget(0, 5)
        self.stream_table.setHorizontalHeaderLabels(["#", "الترميز", "اللغة", "اسم المسار", "طريقة المعالجة"])
        self.stream_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stream_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stream_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stream_table.verticalHeader().setVisible(False)
        self.stream_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.stream_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.stream_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.stream_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.stream_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.stream_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.stream_table.setColumnWidth(0, 64)
        self.stream_table.setColumnWidth(1, 170)
        self.stream_table.setColumnWidth(2, 115)
        self.stream_table.setColumnWidth(4, 190)
        self.stream_table.verticalHeader().setDefaultSectionSize(44)
        self.stream_table.setAlternatingRowColors(True)
        self.stream_table.setMinimumHeight(155)
        self.stream_table.setMaximumHeight(230)
        stream_layout.addWidget(self.stream_table)
        layout.addWidget(stream_card)

        output_card, output_layout = self._card()
        output_layout.addLayout(
            self._section_title(
                "التصدير والمعالجة",
                "حدد مكان الحفظ والصيغة، ثم ابدأ التحويل. يمكن إيقاف المهمة واستئنافها بأمان.",
            )
        )
        output_row = QHBoxLayout()
        output_row.setSpacing(10)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("مسار ملف الترجمة النهائي")
        self.output_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.output_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.output_edit.setClearButtonEnabled(True)
        self.output_edit.textChanged.connect(self.output_edit.setToolTip)
        output_browse = QPushButton("مكان الحفظ", objectName="secondary")
        output_browse.clicked.connect(self._browse_output)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["SRT", "VTT", "ASS", "TXT"])
        self.format_combo.currentTextChanged.connect(self._output_format_changed)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_browse)
        output_row.addWidget(self.format_combo)
        output_layout.addLayout(output_row)
        controls = QHBoxLayout()
        controls.setSpacing(9)
        self.start_button = QPushButton("بدء التحويل  ◀", objectName="primary")
        self.start_button.clicked.connect(self._start_processing)
        self.pause_button = QPushButton("إيقاف مؤقت")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_button = QPushButton("إلغاء المهمة", objectName="danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_processing)
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch(1)
        output_layout.addLayout(controls)
        status_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.processing_status = QLabel("●  جاهز لبدء مشروع جديد", objectName="statusReady")
        self.processing_status.setProperty("tone", "ready")
        self.processing_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_row.addWidget(self.processing_status)
        status_row.addStretch(1)
        self.progress_meta = QLabel("0 / 0 إطار", objectName="progressMeta")
        self.progress_meta.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.progress_meta.setAlignment(Qt.AlignmentFlag.AlignLeft)
        status_row.addWidget(self.progress_meta)
        output_layout.addLayout(status_row)
        output_layout.addWidget(self.progress)
        layout.addWidget(output_card)

        log_card, log_layout = self._card()
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("سجل المعالجة", objectName="sectionTitle"))
        log_header.addStretch(1)
        log_header.addWidget(QLabel("آخر الأحداث والنتائج", objectName="sectionDescription"))
        log_layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(125)
        self.log_view.setPlaceholderText("سيظهر سجل المعالجة هنا…")
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_card)
        return page

    def _build_ai_page(self) -> QWidget:
        page, layout = self._page_shell(
            "محرك الذكاء الاصطناعي",
            "اربط البرنامج بمحرك Vision المحلي واختبر الموديل قبل بدء المعالجة.",
        )
        provider_row = QHBoxLayout()
        provider_row.setSpacing(12)
        assets_dir = Path(__file__).resolve().parents[1] / "assets" / "providers"
        self.provider_cards: dict[str, ProviderCard] = {}
        for engine, name, icon_file, detail in (
            (EngineKind.LM_STUDIO.value, "LM Studio", "lm-studio.png", "خادم محلي متكامل وسهل الاستخدام"),
            (EngineKind.OLLAMA.value, "Ollama", "ollama.png", "تشغيل خفيف وإدارة سريعة للموديلات"),
            (EngineKind.UNSLOTH.value, "Unsloth", "unsloth.png", "تشغيل Vision وGGUF بأداء محلي متقدم"),
        ):
            provider = ProviderCard(engine)
            provider.setObjectName("providerCard")
            provider.setProperty("selected", False)
            provider.setCursor(Qt.CursorShape.PointingHandCursor)
            provider.setToolTip(f"اختيار {name}")
            provider.selected.connect(self._select_provider)
            self.provider_cards[engine] = provider
            provider_layout = QVBoxLayout(provider)
            provider_layout.setContentsMargins(16, 14, 16, 14)
            title_widget = QWidget()
            title_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            title_row = QHBoxLayout(title_widget)
            title_row.setDirection(QBoxLayout.Direction.LeftToRight)
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(9)
            title_row.addStretch(1)
            provider_name = QLabel(name, objectName="providerName")
            provider_name.setAlignment(Qt.AlignmentFlag.AlignRight)
            provider_icon = QLabel(objectName="providerIcon")
            provider_icon.setFixedSize(38, 38)
            provider_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_path = assets_dir / icon_file
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                provider_icon.setPixmap(
                    pixmap.scaled(
                        28,
                        28,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            title_row.addWidget(provider_name)
            title_row.addWidget(provider_icon)
            provider_layout.addWidget(title_widget)
            provider_detail = QLabel(detail, objectName="sectionDescription")
            provider_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
            provider_layout.addWidget(provider_detail)
            provider_row.addWidget(provider)
        layout.addLayout(provider_row)

        runtime_card, runtime_layout = self._card()
        runtime_layout.addLayout(
            self._section_title(
                "إدارة المحرك",
                "اكتشف حالة البرنامج والخادم، وشغّل المحرك المحدد بأمان عند الحاجة.",
            )
        )
        self.runtime_panel = QFrame(objectName="runtimePanel")
        self.runtime_panel.setProperty("tone", "neutral")
        runtime_panel_layout = QHBoxLayout(self.runtime_panel)
        runtime_panel_layout.setContentsMargins(16, 14, 16, 14)
        runtime_text = QVBoxLayout()
        runtime_text.setSpacing(4)
        self.runtime_status_title = QLabel("لم يتم فحص المحرك", objectName="runtimeStatusTitle")
        self.runtime_status_title.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.runtime_status_detail = QLabel(
            "اضغط فحص الحالة للتأكد من التثبيت والخادم.",
            objectName="runtimeStatusDetail",
        )
        self.runtime_status_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.runtime_status_detail.setWordWrap(True)
        self.runtime_executable = QLabel("", objectName="runtimeExecutable")
        self.runtime_executable.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.runtime_executable.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.runtime_executable.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        runtime_text.addWidget(self.runtime_status_title)
        runtime_text.addWidget(self.runtime_status_detail)
        runtime_text.addWidget(self.runtime_executable)
        runtime_panel_layout.addLayout(runtime_text, 1)
        self.runtime_state_badge = QLabel("غير مفحوص", objectName="runtimeStateBadge")
        self.runtime_state_badge.setProperty("tone", "neutral")
        self.runtime_state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        runtime_panel_layout.addWidget(self.runtime_state_badge)
        runtime_layout.addWidget(self.runtime_panel)

        runtime_actions = QHBoxLayout()
        self.runtime_start_button = QPushButton("تشغيل المحرك", objectName="primary")
        self.runtime_start_button.clicked.connect(self._start_runtime)
        self.runtime_stop_button = QPushButton("إيقاف المحرك", objectName="danger")
        self.runtime_stop_button.clicked.connect(self._stop_runtime)
        self.runtime_stop_button.setEnabled(False)
        self.runtime_check_button = QPushButton("فحص الحالة", objectName="secondary")
        self.runtime_check_button.clicked.connect(self._check_runtime)
        self.runtime_browse_button = QPushButton("تحديد ملف التشغيل", objectName="secondary")
        self.runtime_browse_button.clicked.connect(self._browse_runtime_executable)
        runtime_actions.addWidget(self.runtime_start_button)
        runtime_actions.addWidget(self.runtime_stop_button)
        runtime_actions.addWidget(self.runtime_check_button)
        runtime_actions.addWidget(self.runtime_browse_button)
        runtime_actions.addStretch(1)
        runtime_layout.addLayout(runtime_actions)

        runtime_preferences = QHBoxLayout()
        self.auto_start_engine_check = QCheckBox("تشغيل المحرك تلقائيًا عند بدء التحويل")
        self.stop_engine_on_exit_check = QCheckBox("إيقاف المحرك عند إغلاق OCR-AI Studio")
        self.stop_engine_on_exit_check.setToolTip("يُوقف فقط المحرك الذي شغّله هذا التطبيق")
        runtime_preferences.addWidget(self.auto_start_engine_check)
        runtime_preferences.addWidget(self.stop_engine_on_exit_check)
        runtime_preferences.addStretch(1)
        runtime_layout.addLayout(runtime_preferences)
        layout.addWidget(runtime_card)

        card, card_layout = self._card()
        card_layout.addLayout(
            self._section_title(
                "إعدادات الاتصال",
                "اختر الخادم والموديل. سيستخدم البرنامج واجهة OpenAI المتوافقة محليًا.",
            )
        )
        form = QFormLayout()
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("LM Studio", EngineKind.LM_STUDIO.value)
        self.engine_combo.addItem("Ollama", EngineKind.OLLAMA.value)
        self.engine_combo.addItem("Unsloth", EngineKind.UNSLOTH.value)
        self.engine_combo.addItem("OpenAI Compatible", EngineKind.CUSTOM.value)
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        self.url_edit = QLineEdit()
        self.url_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.url_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.url_edit.textEdited.connect(self._invalidate_model_catalog)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMaxVisibleItems(16)
        self.model_combo.currentTextChanged.connect(self._model_selection_changed)
        self.model_edit = self.model_combo.lineEdit()
        self.model_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.model_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.model_edit.setPlaceholderText("qwen/qwen2.5-vl-7b")
        self.api_key_label = QLabel("مفتاح API (اختياري)")
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.api_key_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.api_key_edit.setPlaceholderText("اختياري للخوادم المخصصة — لا يُحفظ على القرص")
        self.api_key_edit.textEdited.connect(self._invalidate_model_catalog)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setSuffix(" ثانية")
        form.addRow("نوع الخادم", self.engine_combo)
        form.addRow("API Base URL", self.url_edit)
        form.addRow("الموديل", self.model_combo)
        form.addRow(self.api_key_label, self.api_key_edit)
        form.addRow("مهلة الطلب", self.timeout_spin)
        card_layout.addLayout(form)
        self.model_catalog_status = QLabel(
            "اضغط تحديث الموديلات لاكتشاف الموديلات المتاحة وحالتها.",
            objectName="sectionDescription",
        )
        self.model_catalog_status.setWordWrap(True)
        self.model_catalog_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        card_layout.addWidget(self.model_catalog_status)
        actions = QHBoxLayout()
        self.refresh_models_button = QPushButton("تحديث الموديلات", objectName="secondary")
        self.refresh_models_button.clicked.connect(self._refresh_models)
        test_button = QPushButton("اختبار الموديل والصورة", objectName="primary")
        test_button.clicked.connect(self._test_model)
        save_button = QPushButton("حفظ الإعدادات", objectName="secondary")
        save_button.clicked.connect(self._save_settings)
        actions.addWidget(self.refresh_models_button)
        actions.addWidget(test_button)
        actions.addWidget(save_button)
        actions.addStretch(1)
        card_layout.addLayout(actions)

        status_card = QFrame(objectName="modelStatusCard")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(14, 12, 14, 12)
        self.model_status = QLabel("لم يتم اختبار الموديل بعد", objectName="modelStatus")
        self.model_status.setProperty("tone", "neutral")
        self.model_status.setWordWrap(True)
        self.model_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_layout.addWidget(self.model_status, 1)
        status_layout.addWidget(QLabel("VISION CHECK", objectName="portBadge"))
        card_layout.addWidget(status_card)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_diagnostics_page(self) -> QWidget:
        page, layout = self._page_shell(
            "فحص جاهزية النظام",
            "تأكد من توفر أدوات الوسائط قبل تشغيل مشروع تحويل طويل.",
        )
        card, card_layout = self._card()
        card_layout.addLayout(
            self._section_title(
                "المكونات الأساسية",
                "يعتمد استخراج المسارات وقراءة الحاويات على FFmpeg وFFprobe.",
            )
        )
        diagnostics = self.media.diagnostics()
        rows = (
            ("FFmpeg", diagnostics["ffmpeg_available"], diagnostics["ffmpeg_path"]),
            ("FFprobe", diagnostics["ffprobe_available"], diagnostics["ffprobe_path"]),
        )
        for name, ready, detail in rows:
            component = QFrame(objectName="diagnosticRow")
            row = QHBoxLayout(component)
            row.setContentsMargins(14, 12, 14, 12)
            text = QVBoxLayout()
            component_name = QLabel(name, objectName="diagnosticName")
            component_name.setAlignment(Qt.AlignmentFlag.AlignRight)
            path = QLabel(str(detail) if ready else "لم يتم العثور على الأداة", objectName="technicalText")
            path.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            path.setAlignment(Qt.AlignmentFlag.AlignLeft)
            text.addWidget(component_name)
            text.addWidget(path)
            row.addLayout(text, 1)
            state = QLabel(
                "جاهز" if ready else "غير متوفر", objectName="readyBadge" if ready else "errorBadge"
            )
            state.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(state)
            card_layout.addWidget(component)
        layout.addWidget(card)

        tip_card, tip_layout = self._card()
        tip_layout.addLayout(
            self._section_title(
                "نصيحة قبل البدء",
                "اختبر موديل Vision من صفحة المحرك، ثم اختر ملف المصدر وانتظر اكتمال كشف المسارات.",
            )
        )
        layout.addWidget(tip_card)
        layout.addStretch(1)
        return page

    def _apply_settings_to_ui(self) -> None:
        index = self.engine_combo.findData(self.settings.engine)
        self.engine_combo.setCurrentIndex(max(index, 0))
        self.url_edit.setText(self.settings.base_url)
        self.model_edit.setText(self.settings.model)
        self.api_key_edit.setText(self.settings.api_key)
        self.timeout_spin.setValue(self.settings.request_timeout_seconds)
        self.auto_start_engine_check.setChecked(self.settings.auto_start_engine)
        self.stop_engine_on_exit_check.setChecked(self.settings.stop_owned_engine_on_exit)
        self.format_combo.setCurrentText(self.settings.export_format.upper())
        self._update_provider_selection()
        self._show_runtime_snapshot(self._inspect_runtime(probe=False))
        self._set_engine_card("pending")

    def _current_settings(self) -> AppSettings:
        output_text = self.output_edit.text().strip()
        return AppSettings(
            engine=str(self.engine_combo.currentData()),
            base_url=self.url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            output_dir=str(Path(output_text).parent) if output_text else "",
            export_format=self.format_combo.currentText().lower(),
            theme="dark",
            max_retries=self.settings.max_retries,
            request_timeout_seconds=self.timeout_spin.value(),
            api_key=self.api_key_edit.text(),
            auto_start_engine=self.auto_start_engine_check.isChecked(),
            stop_owned_engine_on_exit=self.stop_engine_on_exit_check.isChecked(),
            engine_executables=dict(self.settings.engine_executables),
        )

    def _save_settings(self) -> bool:
        try:
            self.settings = self._current_settings()
            self.settings_store.save(self.settings)
        except ValueError as exc:
            QMessageBox.warning(self, "إعدادات غير صحيحة", str(exc))
            return False
        self.statusBar().showMessage("تم حفظ الإعدادات", 3000)
        return True

    @staticmethod
    def _set_tone(label: QLabel, tone: str, text: str) -> None:
        label.setProperty("tone", tone)
        label.setText(text)
        label.style().unpolish(label)
        label.style().polish(label)

    def _set_project_step(self, active_index: int, *, animate: bool = True) -> None:
        if not hasattr(self, "project_steps"):
            return
        for index, step in enumerate(self.project_steps):
            if active_index >= len(self.project_steps) or index < active_index:
                name = "stepDone"
            elif index == active_index:
                name = "stepActive"
            else:
                name = "step"
            previous_state = step.property("stepState")
            step.setProperty("stepState", name)
            step.setObjectName(name)
            step.style().unpolish(step)
            step.style().polish(step)
            if animate and previous_state and previous_state != name:
                self._animate_project_step(index, name == "stepActive")

    def _animate_project_step(self, index: int, active: bool) -> None:
        step = self.project_steps[index]
        effect = step.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(step)
            step.setGraphicsEffect(effect)
        previous = self._step_animations.get(index)
        if previous is not None:
            previous.stop()
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(340 if active else 220)
        animation.setStartValue(0.35 if active else 0.58)
        animation.setKeyValueAt(0.42, 1.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda position=index: self._step_animations.pop(position, None))
        self._step_animations[index] = animation
        animation.start()

    @staticmethod
    def _language_name(code: str) -> str:
        normalized = (code or "und").lower()
        return {
            "ara": "العربية",
            "ar": "العربية",
            "eng": "الإنجليزية",
            "en": "الإنجليزية",
            "fra": "الفرنسية",
            "fre": "الفرنسية",
            "spa": "الإسبانية",
            "ger": "الألمانية",
            "deu": "الألمانية",
            "und": "غير محددة",
        }.get(normalized, normalized.upper())

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, round(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _selected_engine(self) -> EngineKind:
        return EngineKind(str(self.engine_combo.currentData()))

    def _configured_runtime_executable(self, engine: EngineKind | None = None) -> str:
        selected = engine or self._selected_engine()
        return self.settings.engine_executables.get(selected.value, "")

    def _inspect_runtime(self, *, probe: bool) -> RuntimeSnapshot:
        engine = self._selected_engine()
        return self.runtime_manager.inspect(
            engine,
            self.url_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            configured_executable=self._configured_runtime_executable(engine),
            probe=probe,
        )

    def _select_provider(self, engine: str) -> None:
        index = self.engine_combo.findData(engine)
        if index >= 0:
            self.engine_combo.setCurrentIndex(index)

    def _update_provider_selection(self) -> None:
        if not hasattr(self, "provider_cards"):
            return
        selected = str(self.engine_combo.currentData())
        for engine, card in self.provider_cards.items():
            card.setProperty("selected", engine == selected)
            card.style().unpolish(card)
            card.style().polish(card)

    def _set_runtime_busy(self, busy: bool, message: str = "") -> None:
        self._runtime_busy = busy
        self.runtime_check_button.setEnabled(not busy)
        self.runtime_browse_button.setEnabled(not busy)
        if busy:
            self.runtime_start_button.setEnabled(False)
            self.runtime_stop_button.setEnabled(False)
            self.runtime_state_badge.setText("جارٍ التنفيذ")
            self.runtime_state_badge.setProperty("tone", "working")
            self.runtime_status_title.setText(message or "جارٍ فحص المحرك…")
            self.runtime_panel.setProperty("tone", "working")
            for widget in (self.runtime_state_badge, self.runtime_panel):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def _check_runtime(self, _checked: bool = False, *, silent: bool = False) -> None:
        if self._runtime_busy:
            return
        self._runtime_task_token += 1
        token = self._runtime_task_token
        try:
            engine = self._selected_engine()
            base_url = self.url_edit.text().strip()
            api_key = self.api_key_edit.text()
            executable = self._configured_runtime_executable(engine)
        except (ValueError, EngineRuntimeError) as exc:
            self._runtime_task_failed((token, "check", str(exc)))
            return
        self._set_runtime_busy(True, "جارٍ فحص المحرك والخادم…")

        def task() -> None:
            try:
                snapshot = self.runtime_manager.inspect(
                    engine,
                    base_url,
                    api_key=api_key,
                    configured_executable=executable,
                )
                self.bridge.runtime_checked.emit((token, snapshot, silent))
            except Exception as exc:
                self.bridge.runtime_error.emit((token, "check", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _poll_runtime(self) -> None:
        if self._runtime_busy:
            return
        if self.pages.currentIndex() == 1 or (self.worker is not None and self.worker.isRunning()):
            self._check_runtime(silent=True)

    def _start_runtime(self, _checked: bool = False) -> None:
        if self._runtime_busy or not self._save_settings():
            return
        engine = self._selected_engine()
        if engine is EngineKind.CUSTOM:
            QMessageBox.information(
                self,
                "خادم مخصص",
                "الخوادم المخصصة تدعم الاتصال فقط. شغّل الخادم خارجيًا ثم اضغط فحص الحالة.",
            )
            return
        self._runtime_task_token += 1
        token = self._runtime_task_token
        settings = self.settings
        executable = self._configured_runtime_executable(engine)
        self._set_runtime_busy(True, f"جارٍ تشغيل {self._engine_name(engine.value)}…")
        self.runtime_status_detail.setText("سيتم انتظار استجابة API قبل إعلان الجاهزية.")

        def task() -> None:
            try:
                snapshot = self.runtime_manager.ensure_ready(
                    engine,
                    settings.base_url,
                    settings.model,
                    api_key=settings.api_key,
                    configured_executable=executable,
                    auto_start=True,
                    timeout_seconds=min(settings.request_timeout_seconds, 60),
                )
                self.bridge.runtime_action_completed.emit((token, "start", snapshot))
            except Exception as exc:
                self.bridge.runtime_error.emit((token, "start", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _stop_runtime(self, _checked: bool = False) -> None:
        if self._runtime_busy:
            return
        engine = self._selected_engine()
        snapshot = getattr(self, "_last_runtime_snapshot", None)
        if not isinstance(snapshot, RuntimeSnapshot) or not snapshot.owned:
            QMessageBox.information(
                self,
                "حماية المحرك",
                "لن يوقف OCR-AI Studio خادمًا لم يقم هو بتشغيله.",
            )
            return
        self._runtime_task_token += 1
        token = self._runtime_task_token
        executable = self._configured_runtime_executable(engine)
        base_url = self.url_edit.text().strip()
        api_key = self.api_key_edit.text()
        self._set_runtime_busy(True, "جارٍ إيقاف المحرك بأمان…")

        def task() -> None:
            try:
                self.runtime_manager.stop_owned(engine, executable)
                snapshot_after = self.runtime_manager.inspect(
                    engine,
                    base_url,
                    api_key=api_key,
                    configured_executable=executable,
                )
                self.bridge.runtime_action_completed.emit((token, "stop", snapshot_after))
            except Exception as exc:
                self.bridge.runtime_error.emit((token, "stop", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _browse_runtime_executable(self, _checked: bool = False) -> None:
        engine = self._selected_engine()
        if engine is EngineKind.CUSTOM:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "حدد ملف تشغيل المحرك",
            self._configured_runtime_executable(engine),
            "Executable (*.exe *.cmd *.bat);;All files (*.*)",
        )
        if not path:
            return
        self.settings.engine_executables[engine.value] = path
        try:
            self.settings_store.save(self.settings)
        except ValueError as exc:
            QMessageBox.warning(self, "مسار غير صالح", str(exc))
            return
        self._show_runtime_snapshot(self._inspect_runtime(probe=False))

    def _show_runtime_snapshot(self, payload: object) -> None:
        silent = False
        if isinstance(payload, tuple):
            token, snapshot, silent = payload
            if token != self._runtime_task_token:
                return
        else:
            snapshot = payload
        if not isinstance(snapshot, RuntimeSnapshot):
            return
        self._runtime_busy = False
        self._last_runtime_snapshot = snapshot
        presentation = {
            RuntimeState.NOT_INSTALLED: ("error", "غير مثبت", "المحرك غير مكتشف"),
            RuntimeState.STOPPED: ("warning", "متوقف", "الخادم غير متصل"),
            RuntimeState.STARTING: ("working", "جارٍ التشغيل", "جارٍ تشغيل المحرك"),
            RuntimeState.ONLINE: ("success", "متصل", "الخادم جاهز"),
            RuntimeState.ERROR: ("error", "خطأ", "المحرك يحتاج مراجعة"),
        }
        tone, badge, title = presentation[snapshot.state]
        if snapshot.reachable and snapshot.state is RuntimeState.ERROR:
            tone, badge, title = "warning", "يحتاج دخول", "الخادم يعمل ويحتاج مصادقة"
        self.runtime_panel.setProperty("tone", tone)
        self.runtime_state_badge.setProperty("tone", tone)
        self.runtime_state_badge.setText(badge)
        self.runtime_status_title.setText(title)
        self.runtime_status_detail.setText(snapshot.detail)
        self.runtime_executable.setText(snapshot.executable or "اتصال خارجي — لا يوجد ملف تشغيل محلي")
        for widget in (self.runtime_panel, self.runtime_state_badge):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        is_local = snapshot.engine is not EngineKind.CUSTOM
        self.runtime_start_button.setEnabled(
            is_local
            and snapshot.state not in {RuntimeState.NOT_INSTALLED, RuntimeState.ONLINE, RuntimeState.STARTING}
        )
        self.runtime_start_button.setText(
            "إعادة المحاولة" if snapshot.state is RuntimeState.ERROR else "تشغيل المحرك"
        )
        self.runtime_stop_button.setEnabled(snapshot.owned and snapshot.state is not RuntimeState.STOPPED)
        self.runtime_check_button.setEnabled(True)
        self.runtime_browse_button.setEnabled(is_local)
        if snapshot.state is RuntimeState.ONLINE:
            self._set_engine_card("success", "الخادم متصل — اختر الموديل واختبر Vision")
        elif snapshot.state is RuntimeState.NOT_INSTALLED:
            self._set_engine_card("error", "المحرك غير مثبت أو مساره غير معروف")
        elif not silent:
            self._set_engine_card("warning", snapshot.detail)

    def _runtime_action_finished(self, payload: object) -> None:
        token, action, snapshot = payload
        if token != self._runtime_task_token:
            return
        self._show_runtime_snapshot(snapshot)
        if snapshot.state is RuntimeState.ONLINE:
            self.statusBar().showMessage("تم تشغيل المحرك والاتصال بالخادم", 4_000)
            if action == "start":
                self._refresh_models()
        elif action == "stop":
            self.statusBar().showMessage("تم إيقاف المحرك الذي شغّله التطبيق", 4_000)

    def _runtime_task_failed(self, payload: object) -> None:
        token, context, message = payload
        if token != self._runtime_task_token:
            return
        self._runtime_busy = False
        snapshot = RuntimeSnapshot(
            self._selected_engine(),
            RuntimeState.ERROR,
            self._configured_runtime_executable(),
            str(message),
        )
        self._show_runtime_snapshot(snapshot)
        self._append_log("ERROR", f"Runtime {context}: {message}")
        if context == "job":
            self._reset_processing_controls()
            QMessageBox.warning(self, "المحرك غير جاهز", str(message))

    def _engine_changed(self) -> None:
        engine = self.engine_combo.currentData()
        if engine == EngineKind.LM_STUDIO.value:
            self.url_edit.setText("http://127.0.0.1:1234/v1")
        elif engine == EngineKind.OLLAMA.value:
            self.url_edit.setText("http://127.0.0.1:11434/v1")
        elif engine == EngineKind.UNSLOTH.value:
            self.url_edit.setText("http://127.0.0.1:8888/v1")
        self._update_provider_selection()
        self._model_refresh_token += 1
        if hasattr(self, "model_catalog_status"):
            self.model_catalog_status.setText("حدّث القائمة بعد تشغيل الخادم أو تغيير عنوانه.")
        if hasattr(self, "runtime_status_title"):
            self._show_runtime_snapshot(self._inspect_runtime(probe=False))
        self._set_engine_card("pending")

    def _refresh_models(self) -> None:
        if not self._save_settings():
            return
        self._model_refresh_token += 1
        token = self._model_refresh_token
        settings = self.settings
        self.refresh_models_button.setEnabled(False)
        self.model_catalog_status.setText("جاري الاتصال بالخادم واكتشاف الموديلات…")
        self._set_engine_card("working", "جاري اكتشاف الموديلات المتاحة…")

        def task() -> None:
            try:
                models = ModelCatalogClient(
                    EngineKind(settings.engine),
                    settings.base_url,
                    settings.api_key,
                    timeout_seconds=min(settings.request_timeout_seconds, 10),
                ).list_models()
                self.bridge.models_ready.emit((token, models))
            except Exception as exc:
                self.bridge.models_failed.emit((token, str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _invalidate_model_catalog(self, _text: str = "") -> None:
        self._model_refresh_token += 1
        if hasattr(self, "refresh_models_button"):
            self.refresh_models_button.setEnabled(True)
            self.model_catalog_status.setText("تغيّرت إعدادات الاتصال؛ حدّث قائمة الموديلات.")
        self._set_engine_card("pending", "تغيّرت الإعدادات — اختبر الاتصال من جديد")

    def _show_model_catalog(self, payload: object) -> None:
        token, models = payload
        if token != self._model_refresh_token:
            return
        self.refresh_models_button.setEnabled(True)
        current_model = self.model_edit.text().strip()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in models:
            self.model_combo.addItem(model.model_id, model)
        matching_index = self.model_combo.findText(current_model, Qt.MatchFlag.MatchExactly)
        if matching_index >= 0:
            self.model_combo.setCurrentIndex(matching_index)
        else:
            self.model_combo.setEditText(current_model)
        self.model_combo.blockSignals(False)
        if models:
            self.model_catalog_status.setText(f"تم اكتشاف {len(models)} موديل. اختر موديلًا لعرض حالته.")
            self._model_selection_changed(self.model_combo.currentText())
        else:
            self.model_catalog_status.setText("الخادم متصل، لكن لم يُرجع أي موديلات متاحة.")
            self._set_engine_card("warning", "الخادم متصل لكنه لم يُرجع أي موديلات")

    def _show_model_catalog_error(self, payload: object) -> None:
        token, message = payload
        if token != self._model_refresh_token:
            return
        self.refresh_models_button.setEnabled(True)
        self.model_catalog_status.setText(f"تعذر اكتشاف الموديلات: {message}")
        self._set_engine_card("error", "تعذر اكتشاف الموديلات — اضغط للمراجعة")
        self._append_log("ERROR", f"Model catalog: {message}")

    def _model_selection_changed(self, model_id: str) -> None:
        index = self.model_combo.currentIndex()
        model = self.model_combo.itemData(index) if index >= 0 else None
        if not isinstance(model, ModelInfo) or model.model_id != model_id:
            self._set_engine_card("pending")
            return
        details = []
        if model.loaded is True:
            details.append("محمّل في الذاكرة")
        elif model.loaded is False:
            details.append("غير محمّل")
        if model.supports_vision is True:
            details.append("يدعم Vision")
        elif model.supports_vision is False:
            details.append("نصي فقط")
        else:
            details.append("دعم Vision غير معروف — استخدم الاختبار")
        if model.quantization:
            details.append(model.quantization)
        if model.context_length:
            details.append(f"Context {model.context_length:,}")
        if model.size_bytes:
            details.append(f"{model.size_bytes / (1024**3):.1f} GB")
        self.model_catalog_status.setText(f"{model.model_id} — " + " • ".join(details))
        tone = "success" if model.loaded is not False and model.supports_vision is not False else "warning"
        summary = " • ".join(details[:2]) or "الخادم متصل والموديل متاح"
        self._set_engine_card(tone, summary)

    def _output_format_changed(self, format_name: str) -> None:
        output_text = self.output_edit.text().strip()
        if output_text:
            self.output_edit.setText(str(Path(output_text).with_suffix(f".{format_name.lower()}")))

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "اختر ملف الترجمة",
            "",
            "Subtitle sources (*.mks *.mkv *.sup *.sub *.idx);;All files (*.*)",
        )
        if path:
            self.input_edit.setText(path)
            output_dir = Path(self.settings.output_dir) if self.settings.output_dir else Path(path).parent
            self.output_edit.setText(str(output_dir / f"{Path(path).stem}.srt"))
            self._scan_streams()

    def _browse_output(self) -> None:
        extension = self.format_combo.currentText().lower()
        path, _ = QFileDialog.getSaveFileName(
            self, "حفظ الترجمة", self.output_edit.text(), f"{extension.upper()} (*.{extension})"
        )
        if path:
            self.output_edit.setText(path)

    def _scan_streams(self) -> None:
        source = Path(self.input_edit.text().strip())
        if not source.is_file():
            QMessageBox.warning(self, "ملف غير صالح", "اختر ملف مصدر موجودًا أولًا.")
            return
        self._set_project_step(0)
        self._set_tone(self.processing_status, "working", "●  جاري كشف مسارات الترجمة…")

        def task() -> None:
            try:
                self.bridge.streams_ready.emit(self.media.probe_subtitles(source))
            except Exception as exc:
                self.bridge.error.emit(str(exc))

        threading.Thread(target=task, daemon=True).start()

    def _show_streams(self, streams: list[StreamInfo]) -> None:
        self.streams = streams
        self.stream_table.setRowCount(len(streams))
        for row, stream in enumerate(streams):
            kind = "صورية — OCR" if stream.is_bitmap else "نصية — استخراج مباشر"
            title = stream.title.strip() or "بدون اسم"
            values = (
                str(stream.ordinal),
                stream.codec.upper(),
                self._language_name(stream.language),
                title,
                kind,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column in {0, 1, 2, 4}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.stream_table.setItem(row, column, item)
        if streams:
            preferred = next((i for i, stream in enumerate(streams) if stream.is_bitmap), 0)
            self.stream_table.selectRow(preferred)
            self._set_tone(
                self.processing_status,
                "success",
                f"●  تم العثور على {len(streams)} مسار ترجمة",
            )
            self._set_project_step(1)
        else:
            self._set_tone(self.processing_status, "warning", "●  لم يتم العثور على مسارات ترجمة")

    def _test_model(self) -> None:
        if not self._save_settings():
            return
        self._set_tone(self.model_status, "working", "جاري إرسال صورة اختبار فعلية للموديل…")
        self._set_engine_card("working", "جاري اختبار Vision بصورة فعلية…")
        settings = self.settings

        def task() -> None:
            try:
                client = VisionClient(
                    EngineKind(settings.engine),
                    settings.base_url,
                    settings.model,
                    settings.request_timeout_seconds,
                    settings.max_retries,
                    settings.api_key,
                )
                self.bridge.model_checked.emit(client.check_model())
            except Exception as exc:
                self.bridge.model_check_failed.emit(str(exc))

        threading.Thread(target=task, daemon=True).start()

    def _show_model_result(self, result: object) -> None:
        if result.ready and result.supports_vision:
            self._set_tone(
                self.model_status,
                "success",
                f"جاهز ويدعم Vision — {result.model} — زمن الاستجابة {result.latency_ms} ms",
            )
            self._set_engine_card("success", f"Vision جاهز • استجابة {result.latency_ms} ms")
        else:
            self._set_tone(
                self.model_status,
                "error",
                f"الموديل غير جاهز: {result.message}",
            )
            self._set_engine_card("error", "الموديل لم يجتز اختبار Vision")

    def _show_model_check_error(self, message: str) -> None:
        self._set_tone(self.model_status, "error", f"تعذر اختبار الموديل: {message}")
        self._set_engine_card("error", "تعذر الاتصال بالمحرك أو اختبار الموديل")
        self._append_log("ERROR", f"Vision check: {message}")

    def _show_async_error(self, message: str) -> None:
        self._set_tone(self.processing_status, "error", "●  حدث خطأ — راجع السجل")
        self._append_log("ERROR", message)
        QMessageBox.critical(self, "خطأ", message)

    def _selected_stream(self) -> StreamInfo | None:
        row = self.stream_table.currentRow()
        return self.streams[row] if 0 <= row < len(self.streams) else None

    def _start_processing(self) -> None:
        source_text = self.input_edit.text().strip()
        output_text = self.output_edit.text().strip()
        stream = self._selected_stream()
        if not source_text or not Path(source_text).is_file() or not output_text or stream is None:
            QMessageBox.warning(self, "بيانات ناقصة", "حدد الملف ومسار الترجمة والمخرج أولًا.")
            return
        if not self._save_settings():
            return
        output = Path(output_text)
        expected_suffix = f".{self.settings.export_format}"
        if output.suffix.lower() != expected_suffix:
            output = output.with_suffix(expected_suffix)
            self.output_edit.setText(str(output))
        if output.exists():
            answer = QMessageBox.question(
                self,
                "استبدال ملف موجود",
                f"ملف الإخراج موجود بالفعل:\n{output}\n\nهل تريد استبداله؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        request = JobRequest(
            Path(source_text),
            output,
            stream,
            EngineKind(self.settings.engine),
            self.settings.base_url,
            self.settings.model,
            self.settings.export_format,
        )
        if stream.is_bitmap:
            self._prepare_runtime_for_job(request)
        else:
            self._launch_processing(request)

    def _prepare_runtime_for_job(self, request: JobRequest) -> None:
        if self._runtime_busy:
            return
        self._runtime_task_token += 1
        token = self._runtime_task_token
        settings = self.settings
        executable = self._configured_runtime_executable(request.engine)
        self._set_runtime_busy(True, "جارٍ تجهيز محرك Vision للمهمة…")
        self.start_button.setEnabled(False)
        self.start_button.setText("تجهيز المحرك…")
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self._set_tone(self.processing_status, "working", "●  جارٍ التحقق من محرك Vision")
        self._set_engine_card("working", "فحص الخادم قبل بدء المعالجة…")

        def task() -> None:
            try:
                snapshot = self.runtime_manager.ensure_ready(
                    request.engine,
                    request.base_url,
                    request.model,
                    api_key=settings.api_key,
                    configured_executable=executable,
                    auto_start=settings.auto_start_engine,
                    timeout_seconds=min(settings.request_timeout_seconds, 60),
                )
                self.bridge.runtime_job_checked.emit((token, request, snapshot))
            except Exception as exc:
                self.bridge.runtime_error.emit((token, "job", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _runtime_job_finished(self, payload: object) -> None:
        token, request, snapshot = payload
        if token != self._runtime_task_token:
            return
        self._show_runtime_snapshot(snapshot)
        if snapshot.state is not RuntimeState.ONLINE:
            self._reset_processing_controls()
            self._set_tone(self.processing_status, "error", "●  محرك Vision غير جاهز")
            self._append_log("ERROR", f"Runtime readiness: {snapshot.detail}")
            QMessageBox.warning(
                self,
                "المحرك غير جاهز",
                f"تعذر بدء التحويل لأن محرك Vision غير جاهز.\n\n{snapshot.detail}",
            )
            return
        self._launch_processing(request)

    def _launch_processing(self, request: JobRequest) -> None:
        stream = request.stream
        self.worker = ProcessingThread(request, self.settings, self)
        self.worker.status_changed.connect(
            lambda text: self._set_tone(self.processing_status, "working", f"●  {text}")
        )
        self.worker.progress_changed.connect(self._update_progress)
        self.worker.log_received.connect(self._append_log)
        self.worker.cue_received.connect(self._cue_received)
        self.worker.result_ready.connect(self._processing_finished)
        self.worker.failed.connect(self._processing_failed)
        self.start_button.setEnabled(False)
        self.start_button.setText("جارٍ التحويل…")
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("جاري التحضير…")
        self.progress_meta.setText("استخراج صور الترجمة…")
        self._job_started_at = time.monotonic()
        self._set_project_step(2)
        self._set_engine_card("processing", "يعالج صور الترجمة الآن")
        self._append_log("INFO", f"بدء معالجة المسار {stream.ordinal}: {stream.codec}")
        self.worker.start()

    def _update_progress(self, current: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            percent = min(100, round(current * 100 / total))
            self.progress.setFormat(f"{percent}%")
            elapsed = time.monotonic() - self._job_started_at if self._job_started_at else 0
            rate = current / elapsed if current > 0 and elapsed > 0 else 0
            remaining = (total - current) / rate if rate > 0 else 0
            meta = f"{current:,} / {total:,} إطار  •  مضى {self._format_duration(elapsed)}"
            if remaining > 0:
                meta += f"  •  متبقٍ تقريبًا {self._format_duration(remaining)}"
            self.progress_meta.setText(meta)
        else:
            self.progress_meta.setText(f"تمت معالجة {current:,} إطار")
        self._set_tone(self.processing_status, "working", "●  جارٍ استخراج النصوص")

    def _cue_received(self, cue: SubtitleCue) -> None:
        timestamp = self._format_duration(cue.start_ms / 1000)
        self._append_log("OCR", f"{timestamp}  ←  {cue.text.replace(chr(10), ' / ')[:100]}")

    def _toggle_pause(self) -> None:
        if self.worker is None:
            return
        if self._paused:
            self.worker.controller.resume()
            self.pause_button.setText("إيقاف مؤقت")
            self._set_tone(self.processing_status, "working", "●  تم استئناف المعالجة")
            self._set_engine_card("processing", "يعالج صور الترجمة الآن")
        else:
            self.worker.controller.pause()
            self.pause_button.setText("استئناف المعالجة")
            self._set_tone(
                self.processing_status,
                "warning",
                "●  متوقف مؤقتًا — التقدم محفوظ",
            )
            self._set_engine_card("warning", "المعالجة متوقفة مؤقتًا والتقدم محفوظ")
        self._paused = not self._paused

    def _cancel_processing(self) -> None:
        if self.worker is None:
            return
        answer = QMessageBox.question(self, "إلغاء المعالجة", "هل تريد إلغاء العملية؟ سيبقى التقدم محفوظًا.")
        if answer == QMessageBox.StandardButton.Yes:
            self.worker.controller.cancel()
            self._set_tone(self.processing_status, "working", "●  جاري الإلغاء الآمن…")
            self._set_engine_card("working", "جاري إيقاف المعالجة وحفظ التقدم…")

    def _processing_finished(self, result: JobResult) -> None:
        self._reset_processing_controls()
        self.progress.setRange(0, 100)
        if result.status is JobStatus.CANCELLED:
            self.progress.setValue(0)
            self._set_tone(self.processing_status, "warning", "●  تم الإلغاء وحفظ التقدم")
            self._append_log("INFO", f"تم حفظ المشروع للاستئناف: {result.project_id}")
            self.progress_meta.setText(f"تم حفظ {result.completed_frames:,} / {result.total_frames:,} إطار")
            self._set_engine_card("success")
        elif result.status is JobStatus.NEEDS_REVIEW:
            self.progress.setValue(0)
            self._set_tone(self.processing_status, "warning", "●  اكتملت جزئيًا — توجد إطارات للمراجعة")
            self._append_log("WARNING", result.message)
            self.progress_meta.setText(
                f"{result.completed_frames:,} مكتمل • {result.failed_frames:,} يحتاج مراجعة"
            )
            self._set_project_step(3)
            self._set_engine_card("success")
            QMessageBox.warning(
                self,
                "المعالجة تحتاج مراجعة",
                f"تعذر استخراج {result.failed_frames} إطار. حُفظ التقدم ولم يُصدّر ملف ناقص.",
            )
        elif result.status is JobStatus.COMPLETED:
            self.progress.setValue(100)
            self._set_tone(self.processing_status, "success", "●  اكتملت المعالجة بنجاح")
            self._append_log("INFO", f"انتهت المهمة: {result.project_id}")
            self.progress_meta.setText(f"اكتملت معالجة {result.completed_frames:,} إطار")
            self._set_project_step(3)
            self._set_engine_card("success")
        else:
            self.progress.setValue(0)
            self._set_tone(self.processing_status, "error", "●  انتهت المهمة بحالة غير متوقعة")
            self._append_log("ERROR", result.message or result.status.value)
            self.progress_meta.setText("لم تكتمل المهمة")

    def _processing_failed(self, message: str) -> None:
        self._reset_processing_controls()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._set_tone(self.processing_status, "error", "●  فشلت المعالجة — راجع السجل")
        self._append_log("ERROR", message)
        self.progress_meta.setText("توقفت المعالجة بسبب خطأ")
        self._set_engine_card("error", "توقفت المعالجة — راجع سجل الأخطاء")
        QMessageBox.critical(self, "فشل المعالجة", message)

    def _reset_processing_controls(self) -> None:
        self.start_button.setEnabled(True)
        self.start_button.setText("بدء التحويل  ◀")
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.pause_button.setText("إيقاف مؤقت")
        self._paused = False
        self._job_started_at = None

    def _append_log(self, level: str, message: str) -> None:
        self.log_view.appendPlainText(f"[{level:7}] {message}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "إغلاق التطبيق",
                "المعالجة ما زالت تعمل. هل تريد إيقافها وحفظ التقدم ثم الإغلاق؟",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.controller.cancel()
            if not self.worker.wait(5_000):
                self._set_tone(self.processing_status, "working", "●  جاري إنهاء المهمة بأمان…")
                event.ignore()
                return
        if hasattr(self, "runtime_timer"):
            self.runtime_timer.stop()
        if self.stop_engine_on_exit_check.isChecked():
            self.runtime_manager.shutdown_owned(self.settings.engine_executables)
        event.accept()
