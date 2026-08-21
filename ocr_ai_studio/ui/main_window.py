from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ocr_ai_studio import __version__
from ocr_ai_studio.ai.model_catalog import ModelCatalogClient, ModelInfo
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.media = FFmpegService()
        self.bridge = AsyncBridge(self)
        self.bridge.streams_ready.connect(self._show_streams)
        self.bridge.models_ready.connect(self._show_model_catalog)
        self.bridge.models_failed.connect(self._show_model_catalog_error)
        self.bridge.model_checked.connect(self._show_model_result)
        self.bridge.error.connect(self._show_async_error)
        self.streams: list[StreamInfo] = []
        self.worker: ProcessingThread | None = None
        self._paused = False
        self._model_refresh_token = 0

        self.setWindowTitle(f"OCR-AI Studio {__version__}")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(900, 600)
        self.resize(1280, 840)
        self._build_ui()
        self._apply_settings_to_ui()

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

        local_card = QFrame(objectName="localCard")
        local_layout = QVBoxLayout(local_card)
        local_layout.setContentsMargins(13, 12, 13, 12)
        local_title = QLabel("●  خوادم محلية ومتوافقة", objectName="localTitle")
        local_title.setAlignment(Qt.AlignmentFlag.AlignRight)
        privacy = QLabel("وجهة الصور تعتمد على عنوان الخادم المختار", objectName="muted")
        privacy.setWordWrap(True)
        privacy.setAlignment(Qt.AlignmentFlag.AlignRight)
        local_layout.addWidget(local_title)
        local_layout.addWidget(privacy)
        layout.addWidget(local_card)
        version = QLabel(f"OCR-AI Studio  •  {__version__}", objectName="version")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        return sidebar

    def _select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)

    @staticmethod
    def _page_shell(title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)
        if title or description:
            titles = QVBoxLayout()
            if title:
                page_title = QLabel(title, objectName="pageTitle")
                page_title.setAlignment(Qt.AlignmentFlag.AlignRight)
                titles.addWidget(page_title)
            if description:
                page_description = QLabel(description, objectName="pageDescription")
                page_description.setAlignment(Qt.AlignmentFlag.AlignRight)
                titles.addWidget(page_description)
            layout.addLayout(titles)
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
        for number, label, active in (
            ("١", "اختيار المصدر", True),
            ("٢", "تحديد المسار", False),
            ("٣", "المعالجة والتصدير", False),
        ):
            step = QLabel(f"{number}   {label}", objectName="stepActive" if active else "step")
            step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            steps.addWidget(step)
        layout.addLayout(steps)

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
        self.stream_table.setHorizontalHeaderLabels(["المسار", "النوع", "اللغة", "العنوان", "المعالجة"])
        self.stream_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stream_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stream_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stream_table.verticalHeader().setVisible(False)
        self.stream_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.stream_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.stream_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.stream_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.stream_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.stream_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.stream_table.setAlternatingRowColors(True)
        self.stream_table.setMinimumHeight(205)
        stream_layout.addWidget(self.stream_table)
        layout.addWidget(stream_card, 1)

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
        self.progress.setTextVisible(False)
        self.processing_status = QLabel("●  جاهز لبدء مشروع جديد", objectName="statusReady")
        self.processing_status.setProperty("tone", "ready")
        self.processing_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_row.addWidget(self.processing_status)
        status_row.addStretch(1)
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
        self.log_view.setFixedHeight(105)
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
        for name, port, detail in (
            ("LM Studio", "1234", "خادم OpenAI محلي سهل الاستخدام"),
            ("Ollama", "11434", "تشغيل سريع وإدارة موديلات محلية"),
            ("Unsloth", "8888", "تشغيل موديلات Vision وGGUF عبر API محلي"),
        ):
            provider = QFrame(objectName="providerCard")
            provider_layout = QVBoxLayout(provider)
            provider_layout.setContentsMargins(16, 14, 16, 14)
            title_row = QHBoxLayout()
            provider_name = QLabel(name, objectName="providerName")
            provider_name.setAlignment(Qt.AlignmentFlag.AlignRight)
            port_badge = QLabel(f"PORT {port}", objectName="portBadge")
            port_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_row.addWidget(provider_name, 1)
            title_row.addWidget(port_badge)
            provider_layout.addLayout(title_row)
            provider_detail = QLabel(detail, objectName="sectionDescription")
            provider_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
            provider_layout.addWidget(provider_detail)
            provider_row.addWidget(provider)
        layout.addLayout(provider_row)

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
        self.format_combo.setCurrentText(self.settings.export_format.upper())

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

    def _engine_changed(self) -> None:
        engine = self.engine_combo.currentData()
        if engine == EngineKind.LM_STUDIO.value:
            self.url_edit.setText("http://127.0.0.1:1234/v1")
        elif engine == EngineKind.OLLAMA.value:
            self.url_edit.setText("http://127.0.0.1:11434/v1")
        elif engine == EngineKind.UNSLOTH.value:
            self.url_edit.setText("http://127.0.0.1:8888/v1")
        self._model_refresh_token += 1
        if hasattr(self, "model_catalog_status"):
            self.model_catalog_status.setText("حدّث القائمة بعد تشغيل الخادم أو تغيير عنوانه.")

    def _refresh_models(self) -> None:
        if not self._save_settings():
            return
        self._model_refresh_token += 1
        token = self._model_refresh_token
        settings = self.settings
        self.refresh_models_button.setEnabled(False)
        self.model_catalog_status.setText("جاري الاتصال بالخادم واكتشاف الموديلات…")

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

    def _show_model_catalog_error(self, payload: object) -> None:
        token, message = payload
        if token != self._model_refresh_token:
            return
        self.refresh_models_button.setEnabled(True)
        self.model_catalog_status.setText(f"تعذر اكتشاف الموديلات: {message}")
        self._append_log("ERROR", f"Model catalog: {message}")

    def _model_selection_changed(self, model_id: str) -> None:
        index = self.model_combo.currentIndex()
        model = self.model_combo.itemData(index) if index >= 0 else None
        if not isinstance(model, ModelInfo) or model.model_id != model_id:
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
            values = (str(stream.ordinal), stream.codec, stream.language.upper(), stream.title, kind)
            for column, value in enumerate(values):
                self.stream_table.setItem(row, column, QTableWidgetItem(value))
        if streams:
            preferred = next((i for i, stream in enumerate(streams) if stream.is_bitmap), 0)
            self.stream_table.selectRow(preferred)
            self._set_tone(
                self.processing_status,
                "success",
                f"●  تم العثور على {len(streams)} مسار ترجمة",
            )
        else:
            self._set_tone(self.processing_status, "warning", "●  لم يتم العثور على مسارات ترجمة")

    def _test_model(self) -> None:
        if not self._save_settings():
            return
        self._set_tone(self.model_status, "working", "جاري إرسال صورة اختبار فعلية للموديل…")
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
                self.bridge.error.emit(str(exc))

        threading.Thread(target=task, daemon=True).start()

    def _show_model_result(self, result: object) -> None:
        if result.ready and result.supports_vision:
            self._set_tone(
                self.model_status,
                "success",
                f"جاهز ويدعم Vision — {result.model} — زمن الاستجابة {result.latency_ms} ms",
            )
        else:
            self._set_tone(
                self.model_status,
                "error",
                f"الموديل غير جاهز: {result.message}",
            )

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
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self._append_log("INFO", f"بدء معالجة المسار {stream.ordinal}: {stream.codec}")
        self.worker.start()

    def _update_progress(self, current: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
        self._set_tone(self.processing_status, "working", f"●  تمت معالجة {current} إطار")

    def _cue_received(self, cue: SubtitleCue) -> None:
        self._append_log("OCR", f"{cue.start_ms} ms → {cue.text.replace(chr(10), ' / ')[:80]}")

    def _toggle_pause(self) -> None:
        if self.worker is None:
            return
        if self._paused:
            self.worker.controller.resume()
            self.pause_button.setText("إيقاف مؤقت")
            self._set_tone(self.processing_status, "working", "●  تم استئناف المعالجة")
        else:
            self.worker.controller.pause()
            self.pause_button.setText("استئناف المعالجة")
            self._set_tone(
                self.processing_status,
                "warning",
                "●  متوقف مؤقتًا — التقدم محفوظ",
            )
        self._paused = not self._paused

    def _cancel_processing(self) -> None:
        if self.worker is None:
            return
        answer = QMessageBox.question(self, "إلغاء المعالجة", "هل تريد إلغاء العملية؟ سيبقى التقدم محفوظًا.")
        if answer == QMessageBox.StandardButton.Yes:
            self.worker.controller.cancel()
            self._set_tone(self.processing_status, "working", "●  جاري الإلغاء الآمن…")

    def _processing_finished(self, result: JobResult) -> None:
        self._reset_processing_controls()
        self.progress.setRange(0, 100)
        if result.status is JobStatus.CANCELLED:
            self.progress.setValue(0)
            self._set_tone(self.processing_status, "warning", "●  تم الإلغاء وحفظ التقدم")
            self._append_log("INFO", f"تم حفظ المشروع للاستئناف: {result.project_id}")
        elif result.status is JobStatus.NEEDS_REVIEW:
            self.progress.setValue(0)
            self._set_tone(self.processing_status, "warning", "●  اكتملت جزئيًا — توجد إطارات للمراجعة")
            self._append_log("WARNING", result.message)
            QMessageBox.warning(
                self,
                "المعالجة تحتاج مراجعة",
                f"تعذر استخراج {result.failed_frames} إطار. حُفظ التقدم ولم يُصدّر ملف ناقص.",
            )
        elif result.status is JobStatus.COMPLETED:
            self.progress.setValue(100)
            self._set_tone(self.processing_status, "success", "●  اكتملت المعالجة بنجاح")
            self._append_log("INFO", f"انتهت المهمة: {result.project_id}")
        else:
            self.progress.setValue(0)
            self._set_tone(self.processing_status, "error", "●  انتهت المهمة بحالة غير متوقعة")
            self._append_log("ERROR", result.message or result.status.value)

    def _processing_failed(self, message: str) -> None:
        self._reset_processing_controls()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._set_tone(self.processing_status, "error", "●  فشلت المعالجة — راجع السجل")
        self._append_log("ERROR", message)
        QMessageBox.critical(self, "فشل المعالجة", message)

    def _reset_processing_controls(self) -> None:
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.pause_button.setText("إيقاف مؤقت")
        self._paused = False

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
        event.accept()
