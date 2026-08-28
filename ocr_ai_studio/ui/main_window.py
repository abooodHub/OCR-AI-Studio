from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ocr_ai_studio import __version__
from ocr_ai_studio.ai.model_catalog import ModelCatalogClient, ModelInfo
from ocr_ai_studio.ai.runtime_manager import EngineRuntimeManager, RuntimeState
from ocr_ai_studio.ai.vision_client import VisionClient
from ocr_ai_studio.config.settings import AppSettings, SettingsStore
from ocr_ai_studio.domain.models import (
    EngineKind,
    JobRequest,
    JobResult,
    JobStatus,
    QueueStatus,
    StreamInfo,
    SubtitleCue,
)
from ocr_ai_studio.media.ffmpeg import FFmpegService
from ocr_ai_studio.persistence.database import ProjectDatabase
from ocr_ai_studio.processing.pipeline import (
    JobCallbacks,
    JobController,
    PreflightReport,
    ProcessingPipeline,
)

ENGINE_DEFAULTS = {
    EngineKind.LM_STUDIO.value: ("LM Studio", "http://127.0.0.1:1234/v1"),
    EngineKind.OLLAMA.value: ("Ollama", "http://127.0.0.1:11434/v1"),
    EngineKind.UNSLOTH.value: ("Unsloth", "http://127.0.0.1:8888/v1"),
    EngineKind.CUSTOM.value: ("خادم مخصص", "http://127.0.0.1:8000/v1"),
}


class AsyncBridge(QObject):
    streams_ready = Signal(object)
    models_ready = Signal(object)
    readiness_ready = Signal(object)
    job_probe_ready = Signal(object)
    batch_ready = Signal(object)
    error = Signal(str)


class ProcessingThread(QThread):
    status_changed = Signal(str)
    progress_changed = Signal(int, int)
    log_received = Signal(str, str)
    cue_received = Signal(object)
    sample_ready = Signal(object)
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        request: JobRequest,
        settings: AppSettings,
        *,
        confirm_preflight: bool,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.request = request
        self.settings = settings
        self.controller = JobController()
        self.confirm_preflight = confirm_preflight
        self._sample_decision = threading.Event()
        self._sample_approved = True

    def run(self) -> None:
        callbacks = JobCallbacks(
            status=self.status_changed.emit,
            progress=self.progress_changed.emit,
            log=self.log_received.emit,
            cue=self.cue_received.emit,
            preflight=self._confirm_sample,
        )
        try:
            client = VisionClient(
                self.request.engine,
                self.request.base_url,
                self.request.model,
                self.settings.request_timeout_seconds,
                self.settings.max_retries,
                self.settings.api_key,
                self.request.stream.language,
            )
            result = ProcessingPipeline().run(self.request, client, self.controller, callbacks)
            self.result_ready.emit(result)
        except Exception as exc:  # worker boundary
            self.failed.emit(str(exc))

    def _confirm_sample(self, report: PreflightReport) -> bool:
        if not self.confirm_preflight:
            return True
        self._sample_decision.clear()
        self.sample_ready.emit(report)
        self._sample_decision.wait()
        return self._sample_approved

    def decide_preflight(self, approved: bool) -> None:
        self._sample_approved = approved
        self._sample_decision.set()


class DropZone(QFrame):
    files_dropped = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(4)
        self.title = QLabel("اسحب ملف الترجمة أو الفيديو هنا", objectName="dropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        helper = QLabel(
            "MKV / MKS / SUP / IDX+SUB / DVB / XSUB / BDN / DVD",
            objectName="muted",
        )
        helper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addWidget(helper)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class ConnectionDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعدادات الاتصال")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.url_edit = QLineEdit(settings.base_url)
        self.url_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.api_key_edit = QLineEdit(settings.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setSuffix(" ثانية")
        self.timeout_spin.setValue(settings.request_timeout_seconds)
        form.addRow("عنوان API", self.url_edit)
        form.addRow("مفتاح API (اختياري)", self.api_key_edit)
        form.addRow("مهلة الطلب", self.timeout_spin)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.media = FFmpegService()
        self.runtime = EngineRuntimeManager()
        self.bridge = AsyncBridge(self)
        self.bridge.streams_ready.connect(self._show_streams)
        self.bridge.models_ready.connect(self._models_loaded)
        self.bridge.readiness_ready.connect(self._readiness_finished)
        self.bridge.job_probe_ready.connect(self._job_probe_finished)
        self.bridge.batch_ready.connect(self._batch_files_ready)
        self.bridge.error.connect(self._async_error)
        self.streams: list[StreamInfo] = []
        self.worker: ProcessingThread | None = None
        self._project_database: ProjectDatabase | None = None
        self._offscreen_data_dir: tempfile.TemporaryDirectory[str] | None = None
        self._active_queue_id: int | None = None
        self._queue_autorun = False
        self._dvd_title = 1
        self._paused = False
        self._busy_token = 0
        self._busy_frame = 0
        self._job_started_at: float | None = None
        self._recognized_lines = 0
        self._last_output_path: Path | None = None

        self.setWindowTitle(f"OCR-AI Studio {__version__}")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(920, 680)
        self.resize(1180, 820)
        self._build_ui()
        self._apply_settings()

        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(280)
        self.activity_timer.timeout.connect(self._animate_activity)
        QTimer.singleShot(700, self._restore_queue)

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(objectName="content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 26, 34, 32)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title = QLabel("OCR-AI Studio", objectName="appTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignRight)
        subtitle = QLabel(
            "استخراج الترجمة الصورية إلى نص بتوقيت المصدر الأصلي",
            objectName="muted",
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignRight)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        self.connection_status = QLabel("لم يتم فحص المحرك", objectName="connectionStatus")
        self.connection_status.setProperty("tone", "neutral")
        self.connection_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addLayout(title_block, 1)
        header.addWidget(self.connection_status)
        layout.addLayout(header)

        source_card, source_layout = self._card("المصدر", "اختر ملفًا وسيتم كشف مسارات الترجمة تلقائيًا.")
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._accept_dropped_files)
        source_layout.addWidget(self.drop_zone)
        source_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setReadOnly(True)
        self.input_edit.setPlaceholderText("لم يتم اختيار ملف")
        self.input_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        browse = QPushButton("اختيار ملف", objectName="secondary")
        browse.clicked.connect(self._browse_input)
        self.batch_button = QPushButton("إضافة عدة ملفات", objectName="secondary")
        self.batch_button.clicked.connect(self._browse_batch_files)
        dvd = QPushButton("مجلد DVD", objectName="secondary")
        dvd.clicked.connect(self._browse_dvd)
        source_row.addWidget(self.input_edit, 1)
        source_row.addWidget(browse)
        source_row.addWidget(self.batch_button)
        source_row.addWidget(dvd)
        source_layout.addLayout(source_row)
        layout.addWidget(source_card)

        options_card, options_layout = self._card(
            "إعداد التحويل", "اختر المسار والموديل ثم ابدأ. لا توجد إعدادات مطلوبة لمعظم الحالات."
        )
        stream_row = QHBoxLayout()
        stream_label = QLabel("مسار الترجمة")
        self.stream_combo = QComboBox()
        self.stream_combo.setMinimumWidth(360)
        stream_row.addWidget(stream_label)
        stream_row.addWidget(self.stream_combo, 1)
        options_layout.addLayout(stream_row)

        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("المحرك"))
        self.engine_combo = QComboBox()
        for key, (name, _url) in ENGINE_DEFAULTS.items():
            self.engine_combo.addItem(name, key)
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        engine_row.addWidget(self.engine_combo)
        engine_row.addWidget(QLabel("الموديل"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(310)
        self.model_combo.lineEdit().setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        engine_row.addWidget(self.model_combo, 1)
        self.readiness_button = QPushButton("فحص الاتصال وجلب الموديلات", objectName="secondary")
        self.readiness_button.clicked.connect(self._check_readiness)
        engine_row.addWidget(self.readiness_button)
        self.settings_button = QPushButton("إعدادات الاتصال", objectName="linkButton")
        self.settings_button.clicked.connect(self._open_connection_settings)
        engine_row.addWidget(self.settings_button)
        options_layout.addLayout(engine_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("ملف الحفظ"))
        self.output_edit = QLineEdit()
        self.output_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        output_row.addWidget(self.output_edit, 1)
        choose_output = QPushButton("اختيار", objectName="secondary")
        choose_output.clicked.connect(self._browse_output)
        output_row.addWidget(choose_output)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["SRT", "VTT", "ASS", "TXT"])
        self.format_combo.currentTextChanged.connect(self._output_format_changed)
        output_row.addWidget(self.format_combo)
        options_layout.addLayout(output_row)
        layout.addWidget(options_card)

        progress_card, progress_layout = self._card("المعالجة", "التقدم محفوظ ويمكن استئنافه لاحقًا.")
        self.job_status = QLabel("جاهز لبدء مشروع جديد", objectName="jobStatus")
        self.job_status.setProperty("tone", "neutral")
        progress_layout.addWidget(self.job_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0%")
        progress_layout.addWidget(self.progress)
        self.progress_meta = QLabel(
            "الصور 0 / 0   •   الأسطر 0   •   الوقت 00:00",
            objectName="progressMeta",
        )
        self.progress_meta.setAlignment(Qt.AlignmentFlag.AlignRight)
        progress_layout.addWidget(self.progress_meta)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.start_button = QPushButton("بدء التحويل", objectName="primary")
        self.start_button.clicked.connect(self._start_processing)
        self.pause_button = QPushButton("إيقاف مؤقت", objectName="secondary")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_button = QPushButton("إلغاء", objectName="danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_processing)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.start_button)
        progress_layout.addLayout(actions)
        result_row = QHBoxLayout()
        self.result_label = QLabel("", objectName="muted")
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_output_button = QPushButton("فتح مجلد الحفظ", objectName="linkButton")
        self.open_output_button.clicked.connect(self._open_output_directory)
        self.open_output_button.hide()
        result_row.addWidget(self.result_label, 1)
        result_row.addWidget(self.open_output_button)
        progress_layout.addLayout(result_row)
        layout.addWidget(progress_card)

        self.queue_card, queue_layout = self._card(
            "قائمة الانتظار", "تظهر فقط عند وجود أكثر من مهمة نشطة."
        )
        self.queue_table = QTableWidget(0, 3)
        self.queue_table.setHorizontalHeaderLabels(["الملف", "الحالة", "المخرج"])
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        queue_layout.addWidget(self.queue_table)
        queue_actions = QHBoxLayout()
        queue_actions.addStretch(1)
        remove_queue = QPushButton("حذف المحدد", objectName="danger")
        remove_queue.clicked.connect(self._remove_selected_queue_job)
        queue_actions.addWidget(remove_queue)
        queue_layout.addLayout(queue_actions)
        self.queue_card.hide()
        layout.addWidget(self.queue_card)

        self.log_toggle = QPushButton("عرض التفاصيل", objectName="linkButton")
        self.log_toggle.clicked.connect(self._toggle_log)
        layout.addWidget(self.log_toggle, 0, Qt.AlignmentFlag.AlignRight)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(190)
        self.log_view.hide()
        layout.addWidget(self.log_view)
        layout.addStretch(1)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)

    @staticmethod
    def _card(title: str, description: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(objectName="card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = QLabel(title, objectName="sectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignRight)
        helper = QLabel(description, objectName="muted")
        helper.setAlignment(Qt.AlignmentFlag.AlignRight)
        helper.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(helper)
        return card, layout

    def _apply_settings(self) -> None:
        index = self.engine_combo.findData(self.settings.engine)
        self.engine_combo.blockSignals(True)
        self.engine_combo.setCurrentIndex(max(0, index))
        self.engine_combo.blockSignals(False)
        self.model_combo.setEditText(self.settings.model)
        self.format_combo.setCurrentText(self.settings.export_format.upper())

    def _database(self) -> ProjectDatabase:
        if self._project_database is None:
            if os.getenv("QT_QPA_PLATFORM", "").casefold() == "offscreen":
                self._offscreen_data_dir = tempfile.TemporaryDirectory(prefix="ocr-ai-ui-")
                self._project_database = ProjectDatabase(
                    Path(self._offscreen_data_dir.name) / "projects.sqlite3"
                )
            else:
                self._project_database = ProjectDatabase()
        return self._project_database

    def _current_model_id(self) -> str:
        data = self.model_combo.currentData()
        if isinstance(data, ModelInfo):
            return data.model_id
        return self.model_combo.currentText().strip()

    def _save_settings(self) -> bool:
        output = self.output_edit.text().strip()
        try:
            self.settings.engine = str(self.engine_combo.currentData())
            self.settings.model = self._current_model_id()
            self.settings.output_dir = str(Path(output).parent) if output else ""
            self.settings.export_format = self.format_combo.currentText().lower()
            self.settings.validate()
            self.settings_store.save(self.settings)
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "إعدادات غير صحيحة", str(exc))
            return False

    def _engine_changed(self) -> None:
        engine = str(self.engine_combo.currentData())
        self.settings.engine = engine
        self.settings.base_url = ENGINE_DEFAULTS[engine][1]
        self.model_combo.clear()
        self.model_combo.setEditText(self.settings.model)
        self._set_connection("neutral", "لم يتم فحص المحرك")

    def _open_connection_settings(self) -> None:
        dialog = ConnectionDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings.base_url = dialog.url_edit.text().strip()
        self.settings.api_key = dialog.api_key_edit.text()
        self.settings.request_timeout_seconds = dialog.timeout_spin.value()
        self._save_settings()
        self._set_connection("neutral", "تم تحديث الاتصال — اضغط فحص")

    def _check_readiness(self) -> None:
        if not self._save_settings():
            return
        self._busy_token += 1
        token = self._busy_token
        self._busy_frame = 0
        self.readiness_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.activity_timer.start()
        self._set_connection("working", "جارٍ فحص الاتصال")
        settings = self.settings

        def task() -> None:
            try:
                snapshot = self.runtime.inspect(
                    EngineKind(settings.engine), settings.base_url, api_key=settings.api_key
                )
                if snapshot.state is not RuntimeState.ONLINE:
                    self.bridge.readiness_ready.emit((token, snapshot, []))
                    return
                models = ModelCatalogClient(
                    EngineKind(settings.engine),
                    settings.base_url,
                    settings.api_key,
                    min(20, settings.request_timeout_seconds),
                ).list_models()
                self.bridge.readiness_ready.emit((token, snapshot, models))
            except Exception as exc:
                self.bridge.readiness_ready.emit((token, None, exc))

        threading.Thread(target=task, daemon=True).start()

    def _animate_activity(self) -> None:
        self._busy_frame = (self._busy_frame + 1) % 4
        dots = "." * self._busy_frame
        self.readiness_button.setText(f"جارٍ الفحص{dots}")

    def _readiness_finished(self, payload: object) -> None:
        token, snapshot, result = payload
        if token != self._busy_token:
            return
        self.activity_timer.stop()
        self.readiness_button.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.readiness_button.setText("إعادة فحص الاتصال")
        if isinstance(result, Exception):
            self._set_connection("error", "تعذر الاتصال بالمحرك")
            self._append_log("ERROR", str(result))
            return
        if snapshot is None or snapshot.state is not RuntimeState.ONLINE:
            detail = snapshot.detail if snapshot is not None else "الخادم غير متاح"
            self._set_connection("error", "المحرك غير متصل")
            self._append_log("ERROR", detail)
            return
        self._models_loaded((token, result))

    def _models_loaded(self, payload: object) -> None:
        token, models = payload
        if token != self._busy_token:
            return
        current = self._current_model_id() or self.settings.model
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        vision_models = [model for model in models if model.supports_vision is not False]
        for model in vision_models:
            suffix = "  ✓ Vision" if model.supports_vision is True else ""
            self.model_combo.addItem(f"{model.model_id}{suffix}", model)
        match = next(
            (index for index, model in enumerate(vision_models) if model.model_id == current), -1
        )
        if match >= 0:
            self.model_combo.setCurrentIndex(match)
        elif vision_models:
            self.model_combo.setCurrentIndex(0)
        else:
            self.model_combo.setEditText(current)
        self.model_combo.blockSignals(False)
        count = len(vision_models)
        if count:
            self._set_connection("success", f"متصل • {count} موديل Vision")
        else:
            self._set_connection("warning", "متصل • لم يُكتشف موديل Vision")

    def _set_connection(self, tone: str, text: str) -> None:
        self.connection_status.setProperty("tone", tone)
        self.connection_status.setText(text)
        self.connection_status.style().unpolish(self.connection_status)
        self.connection_status.style().polish(self.connection_status)

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "اختر ملف المصدر",
            "",
            "Subtitle sources "
            "(*.mks *.mkv *.sup *.pgs *.sub *.idx *.xml *.m2ts *.mts *.ts "
            "*.avi *.mp4 *.ifo *.vob);;All files (*.*)",
        )
        if path:
            self._accept_source(Path(path))

    def _browse_batch_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "إضافة عدة ملفات",
            "",
            "Subtitle sources "
            "(*.mks *.mkv *.sup *.pgs *.sub *.idx *.xml *.m2ts *.mts *.ts "
            "*.avi *.mp4);;All files (*.*)",
        )
        if not paths or not self._save_settings():
            return
        unique_paths: list[Path] = []
        seen: set[str] = set()
        selected_keys = {str(Path(path).resolve()).casefold() for path in paths}
        for raw_path in paths:
            source = Path(raw_path)
            key = str(source.resolve()).casefold()
            companion_idx = str(source.with_suffix(".idx").resolve()).casefold()
            if key in seen or (source.suffix.casefold() == ".sub" and companion_idx in selected_keys):
                continue
            seen.add(key)
            unique_paths.append(source)
        self.batch_button.setEnabled(False)
        self.batch_button.setText("جارٍ تحليل الملفات…")
        settings = AppSettings(
            engine=self.settings.engine,
            base_url=self.settings.base_url,
            model=self.settings.model,
            output_dir=self.settings.output_dir,
            export_format=self.settings.export_format,
            theme=self.settings.theme,
            max_retries=self.settings.max_retries,
            request_timeout_seconds=self.settings.request_timeout_seconds,
            api_key=self.settings.api_key,
        )

        def task() -> None:
            requests: list[JobRequest] = []
            errors: list[str] = []
            reserved_outputs: set[Path] = set()
            for source in unique_paths:
                try:
                    streams = self.media.probe_subtitles(source)
                    if not streams:
                        raise ValueError("لا يحتوي على مسارات ترجمة")
                    stream = next((item for item in streams if item.is_bitmap), streams[0])
                    output_root = (
                        Path(settings.output_dir)
                        if settings.output_dir
                        else source.parent
                    )
                    candidate = output_root / f"{source.stem}.ocr.{settings.export_format}"
                    output = self._unused_output_path(candidate, reserved_outputs)
                    reserved_outputs.add(output)
                    requests.append(
                        JobRequest(
                            source,
                            output,
                            stream,
                            EngineKind(settings.engine),
                            settings.base_url,
                            settings.model,
                            settings.export_format,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{source.name}: {exc}")
            self.bridge.batch_ready.emit((requests, errors))

        threading.Thread(target=task, daemon=True).start()

    @staticmethod
    def _unused_output_path(candidate: Path, reserved: set[Path] | None = None) -> Path:
        reserved = reserved or set()
        if not candidate.exists() and candidate not in reserved:
            return candidate
        for number in range(2, 10_000):
            alternate = candidate.with_name(f"{candidate.stem}-{number}{candidate.suffix}")
            if not alternate.exists() and alternate not in reserved:
                return alternate
        raise ValueError(f"تعذر إنشاء اسم حفظ آمن للملف {candidate.name}")

    def _batch_files_ready(self, payload: object) -> None:
        requests, errors = payload
        self.batch_button.setEnabled(True)
        self.batch_button.setText("إضافة عدة ملفات")
        for request in requests:
            self._database().enqueue(request)
        self._refresh_queue()
        if requests:
            self._queue_autorun = True
            self._set_job_status("success", f"أضيف {len(requests)} ملف إلى قائمة الانتظار")
            self._append_log("INFO", f"أضيف {len(requests)} ملف إلى قائمة الانتظار")
            self._run_next_queue_job()
        if errors:
            QMessageBox.warning(
                self,
                "تعذر إضافة بعض الملفات",
                "\n".join(errors[:12]),
            )

    def _browse_dvd(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "اختر مجلد VIDEO_TS أو DVD")
        if path:
            self._accept_source(Path(path))

    def _accept_dropped_files(self, paths: object) -> None:
        if paths:
            self._accept_source(paths[0])

    def _accept_source(self, source: Path) -> None:
        if source.suffix.casefold() in {".ifo", ".vob"} and (
            source.parent / "VIDEO_TS.IFO"
        ).is_file():
            source = source.parent
        if source.is_dir():
            dvd_root = source / "VIDEO_TS" if (source / "VIDEO_TS").is_dir() else source
            if not (dvd_root / "VIDEO_TS.IFO").is_file():
                QMessageBox.warning(self, "مجلد غير صالح", "لم يتم العثور على VIDEO_TS.IFO.")
                return
            source = dvd_root
            title, accepted = QInputDialog.getInt(
                self,
                "عنوان DVD",
                "اختر رقم عنوان الفيلم (Title):",
                1,
                1,
                99,
            )
            if not accepted:
                return
            self._dvd_title = title
        else:
            self._dvd_title = 1
        self.input_edit.setText(str(source))
        output_root = Path(self.settings.output_dir) if self.settings.output_dir else source.parent
        name = f"{source.parent.name}-title-{self._dvd_title}" if source.is_dir() else source.stem
        self.output_edit.setText(str(output_root / f"{name}.ocr.{self.format_combo.currentText().lower()}"))
        self.drop_zone.title.setText(source.name or str(source))
        self._scan_streams()

    def _scan_streams(self) -> None:
        source = Path(self.input_edit.text().strip())
        if not source.exists():
            return
        self.stream_combo.clear()
        self.stream_combo.addItem("جارٍ تحليل المسارات…")
        self.stream_combo.setEnabled(False)

        def task() -> None:
            try:
                self.bridge.streams_ready.emit(
                    self.media.probe_subtitles(source, dvd_title=self._dvd_title)
                )
            except Exception as exc:
                self.bridge.error.emit(str(exc))

        threading.Thread(target=task, daemon=True).start()

    def _show_streams(self, streams: list[StreamInfo]) -> None:
        self.streams = streams
        self.stream_combo.clear()
        for stream in streams:
            kind = "OCR صوري" if stream.is_bitmap else "نصي مباشر"
            language = self._language_name(stream.language)
            self.stream_combo.addItem(
                f"{stream.ordinal + 1} — {language} — {stream.codec} — {kind}", stream
            )
        self.stream_combo.setEnabled(bool(streams))
        if streams:
            preferred = next((i for i, stream in enumerate(streams) if stream.is_bitmap), 0)
            self.stream_combo.setCurrentIndex(preferred)
            self._set_job_status("success", f"تم العثور على {len(streams)} مسار ترجمة")
        else:
            self._set_job_status("warning", "لم يتم العثور على مسارات ترجمة")

    def _selected_stream(self) -> StreamInfo | None:
        data = self.stream_combo.currentData()
        return data if isinstance(data, StreamInfo) else None

    def _browse_output(self) -> None:
        extension = self.format_combo.currentText().lower()
        path, _ = QFileDialog.getSaveFileName(
            self, "حفظ الترجمة", self.output_edit.text(), f"{extension.upper()} (*.{extension})"
        )
        if path:
            self.output_edit.setText(path)

    def _output_format_changed(self, format_name: str) -> None:
        if self.output_edit.text().strip():
            self.output_edit.setText(
                str(Path(self.output_edit.text().strip()).with_suffix(f".{format_name.lower()}"))
            )

    def _start_processing(self) -> None:
        source = Path(self.input_edit.text().strip())
        output_text = self.output_edit.text().strip()
        stream = self._selected_stream()
        if not source.exists() or not output_text or stream is None:
            QMessageBox.warning(self, "بيانات ناقصة", "اختر المصدر ومسار الترجمة وملف الحفظ.")
            return
        if stream.is_bitmap and not self._current_model_id():
            QMessageBox.warning(self, "الموديل غير محدد", "اختر موديل Vision أولًا.")
            return
        if not self._save_settings():
            return
        self.result_label.clear()
        self.open_output_button.hide()
        output = Path(output_text).with_suffix(f".{self.settings.export_format}")
        self.output_edit.setText(str(output))
        if output.exists():
            answer = QMessageBox.question(
                self,
                "استبدال الملف",
                f"ملف الحفظ موجود بالفعل:\n{output}\n\nهل تريد استبداله؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        request = JobRequest(
            source,
            output,
            stream,
            EngineKind(self.settings.engine),
            self.settings.base_url,
            self.settings.model,
            self.settings.export_format,
        )
        self._active_queue_id = self._database().enqueue(request)
        self._queue_autorun = True
        self._refresh_queue()
        self._run_next_queue_job()

    def _restore_queue(self) -> None:
        self._refresh_queue()
        pending = [
            job for job in self._database().queue_jobs(active_only=True)
            if job.status is QueueStatus.QUEUED
        ]
        if pending:
            self._queue_autorun = True
            self._append_log("INFO", f"تم استعادة {len(pending)} مهمة محفوظة")
            self._run_next_queue_job()

    def _run_next_queue_job(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        pending = [
            job for job in self._database().queue_jobs(active_only=True)
            if job.status is QueueStatus.QUEUED
        ]
        if not pending:
            self._active_queue_id = None
            self._queue_autorun = False
            self._refresh_queue()
            return
        job = pending[0]
        if not job.request.input_path.exists():
            self._database().set_queue_status(
                job.id, QueueStatus.FAILED, error="ملف المصدر لم يعد موجودًا"
            )
            QTimer.singleShot(0, self._run_next_queue_job)
            return
        self._active_queue_id = job.id
        self._database().set_queue_status(job.id, QueueStatus.RUNNING)
        self._refresh_queue()
        if job.request.stream.is_bitmap:
            self._probe_before_job(job.request)
        else:
            self._launch_processing(job.request)

    def _probe_before_job(self, request: JobRequest) -> None:
        self.start_button.setEnabled(False)
        self._set_job_status("working", "جارٍ التحقق من محرك Vision…")

        def task() -> None:
            try:
                snapshot = self.runtime.inspect(
                    request.engine, request.base_url, api_key=self.settings.api_key
                )
                self.bridge.job_probe_ready.emit((snapshot, request))
            except Exception as exc:
                self.bridge.job_probe_ready.emit((None, (request, exc)))

        threading.Thread(target=task, daemon=True).start()

    def _job_probe_finished(self, payload: object) -> None:
        snapshot, result = payload
        if isinstance(result, tuple):
            request, error = result
            self._job_probe_failed(request, str(error))
            return
        request = result
        if snapshot is None or snapshot.state is not RuntimeState.ONLINE:
            self._job_probe_failed(request, snapshot.detail if snapshot else "الخادم غير متاح")
            return
        self._set_connection("success", "المحرك متصل")
        self._launch_processing(request)

    def _job_probe_failed(self, _request: JobRequest, message: str) -> None:
        if self._active_queue_id is not None:
            self._database().set_queue_status(
                self._active_queue_id, QueueStatus.QUEUED, error=message
            )
        self._active_queue_id = None
        self.start_button.setEnabled(True)
        self._set_connection("error", "المحرك غير متصل")
        self._set_job_status("warning", "شغّل محرك Vision ثم ستُستأنف المهمة")
        self._append_log("ERROR", message)
        if self._queue_autorun:
            QTimer.singleShot(10_000, self._run_next_queue_job)

    def _launch_processing(self, request: JobRequest) -> None:
        active_jobs = [
            job
            for job in self._database().queue_jobs(active_only=True)
            if job.status in {QueueStatus.QUEUED, QueueStatus.RUNNING}
        ]
        self.worker = ProcessingThread(
            request,
            self.settings,
            confirm_preflight=len(active_jobs) <= 1,
            parent=self,
        )
        self.worker.status_changed.connect(lambda text: self._set_job_status("working", text))
        self.worker.progress_changed.connect(self._update_progress)
        self.worker.log_received.connect(self._append_log)
        self.worker.cue_received.connect(self._cue_received)
        self.worker.sample_ready.connect(self._show_preflight_result)
        self.worker.result_ready.connect(self._processing_finished)
        self.worker.failed.connect(self._processing_failed)
        self._job_started_at = time.monotonic()
        self._recognized_lines = 0
        self.result_label.clear()
        self.open_output_button.hide()
        self.progress.setRange(0, 0)
        self.progress.setFormat("جارٍ التحضير…")
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._set_job_status("working", "جارٍ تجهيز صور الترجمة…")
        self.worker.start()

    def _show_preflight_result(self, report: PreflightReport) -> None:
        if self.worker is None:
            return
        estimate = (
            self._format_duration(report.estimated_seconds)
            if report.estimated_seconds > 0
            else "غير معروف"
        )
        samples = "\n".join(f"• {text[:120]}" for text in report.texts[:3])
        summary = (
            f"نجح OCR في {report.recognized_frames} من {report.sampled_frames} صور.\n"
            f"الوقت المتوقع للمسار: {estimate}."
        )
        if report.recognized_frames == 0:
            QMessageBox.warning(
                self,
                "فشل اختبار العينة",
                summary + "\n\nلم يُستخرج أي نص، لذلك أُوقفت المهمة قبل إضاعة الوقت.",
            )
            self.worker.decide_preflight(False)
            return
        answer = QMessageBox.question(
            self,
            "نتيجة اختبار العينة",
            summary + (f"\n\nالنص المستخرج:\n{samples}" if samples else "")
            + "\n\nهل تريد متابعة التحويل؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        self.worker.decide_preflight(answer == QMessageBox.StandardButton.Yes)

    def _update_progress(self, current: int, total: int) -> None:
        elapsed = time.monotonic() - self._job_started_at if self._job_started_at else 0.0
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.progress.setFormat(f"{min(100, round(current * 100 / total))}%")
        rate = current / elapsed if current > 0 and elapsed > 0 else 0.0
        remaining = (total - current) / rate if total > current and rate > 0 else 0.0
        text = (
            f"الصور {current:,} / {total:,}   •   الأسطر {self._recognized_lines:,}"
            f"   •   الوقت {self._format_duration(elapsed)}"
        )
        if remaining > 0:
            text += f"   •   المتبقي تقريبًا {self._format_duration(remaining)}"
        self.progress_meta.setText(text)

    def _cue_received(self, cue: SubtitleCue) -> None:
        self._recognized_lines += max(1, len([line for line in cue.text.splitlines() if line.strip()]))
        self._append_log(
            "OCR", f"{self._format_duration(cue.start_ms / 1000)} ← {cue.text.replace(chr(10), ' / ')[:120]}"
        )

    def _toggle_pause(self) -> None:
        if self.worker is None:
            return
        if self._paused:
            self.worker.controller.resume()
            self.pause_button.setText("إيقاف مؤقت")
            self._set_job_status("working", "تم استئناف المعالجة")
        else:
            self.worker.controller.pause()
            self.pause_button.setText("استئناف")
            self._set_job_status("warning", "متوقف مؤقتًا — التقدم محفوظ")
        self._paused = not self._paused

    def _cancel_processing(self) -> None:
        if self.worker is not None:
            self.worker.controller.cancel()
            self._set_job_status("working", "جارٍ الإلغاء الآمن…")

    def _processing_finished(self, result: JobResult) -> None:
        queue_id = self._active_queue_id
        elapsed = time.monotonic() - self._job_started_at if self._job_started_at else 0.0
        if queue_id is not None:
            status = QueueStatus.COMPLETED if result.status is JobStatus.COMPLETED else QueueStatus.FAILED
            if result.status is JobStatus.CANCELLED:
                status = QueueStatus.CANCELLED
            self._database().set_queue_status(
                queue_id, status, project_id=result.project_id, error=result.message
            )
        self._reset_controls()
        if result.status is JobStatus.COMPLETED:
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self._set_job_status("success", "اكتمل استخراج الترجمة وحفظها بنجاح")
            if result.output_path is not None:
                self._last_output_path = result.output_path
                self.result_label.setText(str(result.output_path))
                self.open_output_button.show()
            if result.quality is not None:
                timing_label = {
                    "validated": "متحقق",
                    "source_native": "أصلي",
                    "manually_adjusted": "معدّل",
                }.get(result.quality.timing_status, result.quality.timing_status)
                self.progress_meta.setText(
                    f"الصور {result.total_frames:,}   •   الأسطر "
                    f"{result.quality.recognized_lines:,}   •   الوقت "
                    f"{self._format_duration(elapsed)}   •   التوقيت {timing_label}"
                )
                timing_notes = (
                    result.quality.overlaps
                    + result.quality.suspicious_short
                    + result.quality.suspicious_long
                )
                if timing_notes:
                    self.progress_meta.setText(
                        self.progress_meta.text() + f"   •   ملاحظات التوقيت {timing_notes:,}"
                    )
        elif result.status is JobStatus.CANCELLED:
            self._set_job_status("warning", "تم الإلغاء وحُفظ التقدم للاستئناف")
            self.progress_meta.setText(
                f"توقف بعد {result.completed_frames:,} من {result.total_frames:,} صورة   •   "
                f"الوقت {self._format_duration(elapsed)}"
            )
            if result.output_path is not None:
                self._last_output_path = result.output_path
                self.result_label.setText(f"تم حفظ النص الجزئي: {result.output_path}")
                self.open_output_button.show()
        else:
            self._set_job_status("error", result.message or "لم تكتمل المهمة")
            if result.output_path is not None:
                self._last_output_path = result.output_path
                self.result_label.setText(f"تم حفظ النص الجزئي: {result.output_path}")
                self.open_output_button.show()
        self._active_queue_id = None
        self._refresh_queue()
        if self._queue_autorun:
            QTimer.singleShot(300, self._run_next_queue_job)

    def _processing_failed(self, message: str) -> None:
        transient = any(
            token in message.casefold()
            for token in ("connection", "timeout", "timed out", "connect", "unavailable")
        )
        if self._active_queue_id is not None:
            self._database().set_queue_status(
                self._active_queue_id,
                QueueStatus.QUEUED if transient else QueueStatus.FAILED,
                error=message,
            )
        self._active_queue_id = None
        self._reset_controls()
        self._append_log("ERROR", message)
        if transient:
            self._set_job_status("warning", "انقطع الخادم — ستُستأنف المهمة تلقائيًا")
            QTimer.singleShot(10_000, self._run_next_queue_job)
        else:
            self._set_job_status("error", "فشلت المعالجة — افتح التفاصيل")
            QMessageBox.critical(self, "فشل المعالجة", message)
            if self._queue_autorun:
                QTimer.singleShot(300, self._run_next_queue_job)
        self._refresh_queue()

    def _reset_controls(self) -> None:
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.pause_button.setText("إيقاف مؤقت")
        self._paused = False
        self._job_started_at = None

    def _refresh_queue(self) -> None:
        jobs = self._database().queue_jobs(active_only=True)
        visible_jobs = [
            job for job in jobs if job.status in {QueueStatus.QUEUED, QueueStatus.RUNNING}
        ]
        self.queue_card.setVisible(len(visible_jobs) > 1)
        self.queue_table.setRowCount(len(visible_jobs))
        status_names = {
            QueueStatus.QUEUED: "بانتظار التشغيل",
            QueueStatus.RUNNING: "جارٍ المعالجة",
        }
        for row, job in enumerate(visible_jobs):
            values = (job.request.input_path.name, status_names[job.status], job.request.output_path.name)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                self.queue_table.setItem(row, column, item)

    def _remove_selected_queue_job(self) -> None:
        row = self.queue_table.currentRow()
        if row < 0:
            return
        item = self.queue_table.item(row, 0)
        if item is None:
            return
        queue_id = int(item.data(Qt.ItemDataRole.UserRole))
        if queue_id == self._active_queue_id:
            QMessageBox.information(self, "المهمة تعمل", "أوقف المهمة الحالية قبل حذفها.")
            return
        self._database().remove_queue_job(queue_id)
        self._refresh_queue()

    def _open_output_directory(self) -> None:
        if self._last_output_path is None:
            return
        directory = self._last_output_path.parent
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _toggle_log(self) -> None:
        show = self.log_view.isHidden()
        self.log_view.setVisible(show)
        self.log_toggle.setText("إخفاء التفاصيل" if show else "عرض التفاصيل")

    def _append_log(self, level: str, message: str) -> None:
        self.log_view.appendPlainText(f"[{level:5}] {message}")

    def _async_error(self, message: str) -> None:
        self.stream_combo.clear()
        self.stream_combo.setEnabled(False)
        self._set_job_status("error", "تعذر قراءة المصدر")
        self._append_log("ERROR", message)
        QMessageBox.critical(self, "خطأ", message)

    def _set_job_status(self, tone: str, text: str) -> None:
        self.job_status.setProperty("tone", tone)
        self.job_status.setText(text)
        self.job_status.style().unpolish(self.job_status)
        self.job_status.style().polish(self.job_status)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, round(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _language_name(code: str) -> str:
        return {
            "ara": "العربية",
            "ar": "العربية",
            "eng": "الإنجليزية",
            "en": "الإنجليزية",
            "fra": "الفرنسية",
            "spa": "الإسبانية",
            "deu": "الألمانية",
            "und": "غير محددة",
        }.get((code or "und").casefold(), (code or "und").upper())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "إغلاق البرنامج",
                "المعالجة مستمرة. هل تريد حفظ التقدم والإغلاق؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._active_queue_id is not None:
                self._database().set_queue_status(self._active_queue_id, QueueStatus.QUEUED)
            self.worker.controller.cancel()
            if not self.worker.wait(5_000):
                event.ignore()
                return
        event.accept()
