"""
gui/app.py — SubAIMasterPro Main Graphical User Interface
"""

import json
import logging
import os
import queue
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.ai_client import AIClient
from core.image_processor import ImageProcessor
from core.media_engine import MediaEngine
from core.srt_builder import SRTBuilder
from gui.styles import C, apply_styles
from utils.config import load_config, save_config
from utils.version import VERSION as APP_VERSION

logger = logging.getLogger("gui.app")


class SubAIMasterPro:
    """Commercial-grade subtitle OCR master application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"OCR-AI ✦ v{APP_VERSION}")
        self.root.minsize(940, 740)
        self.root.configure(bg=C["bg"])

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self._q: queue.Queue = queue.Queue()
        self._stop: threading.Event = threading.Event()
        self._pause_event: threading.Event = threading.Event()
        self._pause_event.set()
        self._is_running: bool = False
        self._worker_thread: threading.Thread | None = None

        self.config = load_config()
        self.selected_stream_idx = int(self.config.get("stream_idx", "0"))
        self.stream_mapping: dict[int, int] = {}
        self.stream_info_dict: dict[int, dict] = {}

        self.model_status_var = tk.StringVar(value="غير جاهز")
        self.autoscroll_var = tk.BooleanVar(value=True)

        apply_styles(self.root)
        self._build_ui()
        self._poll_queue()

    def _save_config(self) -> None:
        self.config["output_dir"] = self.output_var.get()
        self.config["stream_idx"] = str(self.selected_stream_idx)
        self.config["export_format"] = self.export_fmt_var.get()
        if hasattr(self, "lms_url_var"):
            self.config["lmstudio_url"] = self.lms_url_var.get()
        if hasattr(self, "model_var"):
            self.config["lmstudio_model"] = self.model_var.get()

        save_config(self.config)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.grid(row=0, column=0, sticky="nsew")

        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=0)
        outer.rowconfigure(1, weight=0)
        outer.rowconfigure(2, weight=0)
        outer.rowconfigure(3, weight=1)

        # Header Banner
        header_frame = ttk.Frame(outer, style="Panel.TFrame", padding=(16, 12))
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header_frame.columnconfigure(0, weight=1)
        header_frame.columnconfigure(1, weight=0)

        header_left = ttk.Frame(header_frame, style="Panel.TFrame")
        header_left.grid(row=0, column=0, sticky="w")

        ttk.Label(header_left, text="✦ OCR-AI", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(header_left, text="تحويل الترجمة الصورية إلى نص بالذكاء الاصطناعي", style="HeaderSub.TLabel").pack(anchor="w", pady=(2, 0))

        logo_frame = ttk.Frame(header_frame, style="Panel.TFrame")
        logo_frame.grid(row=0, column=1, sticky="e")

        ttk.Button(logo_frame, text="⚙️ حول التطبيق", command=self._show_about_dialog, width=14).pack(side="right", padx=(8, 0))
        tk.Label(logo_frame, text=f" v{APP_VERSION} ", bg="#1e1b4b", fg=C["accent2"],
                 font=("Segoe UI", 9, "bold"), padx=8, pady=4, relief="flat").pack(side="right", padx=4)
        tk.Label(logo_frame, text=" 🎬 OCR-AI ENGINE ", bg=C["accent_subtle"], fg=C["accent2"],
                 font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat").pack(side="right")

        # Cards Grid
        cards_grid = ttk.Frame(outer)
        cards_grid.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        cards_grid.columnconfigure(0, weight=1)
        cards_grid.columnconfigure(1, weight=1)
        cards_grid.rowconfigure(0, weight=1)

        self._build_files_card(cards_grid)
        self._build_engine_card(cards_grid)

        # Dashboard & Progress
        self._build_controls_and_progress(outer)

        # Terminal Log
        self._build_log(outer)

    def _show_about_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("حول تطبيق OCR-AI")
        win.geometry("520x420")
        win.configure(bg=C["panel"])
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        win.update_idletasks()
        rx = self.root.winfo_x() + (self.root.winfo_width() // 2) - 260
        ry = self.root.winfo_y() + (self.root.winfo_height() // 2) - 210
        win.geometry(f"+{rx}+{ry}")

        container = ttk.Frame(win, style="Panel.TFrame", padding=20)
        container.pack(fill="both", expand=True)

        tk.Label(container, text="🎬  OCR-AI Engine Pro", bg=C["panel"], fg=C["accent2"],
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 4))
        tk.Label(container, text=f"الإصدار الرقمي الرسمي: v{APP_VERSION}", bg=C["panel"], fg=C["green"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 12))

        desc = (
            "محرك سينمائي متقدم لتفريغ وتحويل مسارات الترجمة الصورية (PGS / HDMV / VobSub) "
            "والنصية (SubRip / ASS / VTT) إلى نصوص دقيقة باستخدام رؤية الذكاء الاصطناعي المحلية."
        )
        tk.Label(container, text=desc, bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 10), wraplength=470, justify="right").pack(anchor="w", pady=(0, 14))

        features_box = tk.LabelFrame(container, text="  🌟 الميزات والتقنيات المدعومة  ",
                                    bg=C["card"], fg=C["accent2"], padx=12, pady=10, relief="solid", bd=1)
        features_box.pack(fill="x", pady=(0, 16))

        feats = [
            "• استخراج فوري للمسارات النصية (SRT / ASS) في أقل من ثانية.",
            "• رؤية ذكية للترجمات الصورية عبر LM Studio و Ollama.",
            "• دعم التوقف المؤقت والاستئناف التلقائي مع حفظ كاش الجلسة (.cache).",
            "• تصدير متعدد الصيغ: SubRip (.srt), WebVTT (.vtt), ASS (.ass), TXT (.txt).",
        ]
        for f in feats:
            tk.Label(features_box, text=f, bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=2)

        ttk.Button(container, text="إغلاق النافذة", command=win.destroy, width=14).pack(anchor="e")

    def _build_files_card(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="  📁  الملفات وصيغة التصدير ومسار الترجمة  ", padding=14)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        lf.columnconfigure(1, weight=1)

        tk.Label(lf, text="ملف الترجمة (MKS / MKV / SUB):", bg=C["card"], fg=C["text_dim"],
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=4)
        self.input_var = tk.StringVar()
        e_in = ttk.Entry(lf, textvariable=self.input_var, state="readonly")
        e_in.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(lf, text="استعراض …", command=self._browse_input, width=11).grid(row=0, column=2, sticky="e", pady=4)

        tk.Label(lf, text="مجلد الحفظ:", bg=C["card"], fg=C["text_dim"],
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=4)
        self.output_var = tk.StringVar(value=self.config.get("output_dir", ""))
        e_out = ttk.Entry(lf, textvariable=self.output_var, state="readonly")
        e_out.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(lf, text="استعراض …", command=self._browse_output, width=11).grid(row=1, column=2, sticky="e", pady=4)

        tk.Label(lf, text="صيغة المخرج النهائي:", bg=C["card"], fg=C["text_dim"],
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=4)
        self.export_fmt_var = tk.StringVar(value=self.config.get("export_format", "SRT"))
        fmt_combo = ttk.Combobox(lf, textvariable=self.export_fmt_var, values=["SRT", "VTT", "ASS", "TXT"], state="readonly", width=12)
        fmt_combo.grid(row=2, column=1, sticky="w", padx=(8, 8), pady=4)
        fmt_combo.bind("<<ComboboxSelected>>", lambda e: self._save_config())

        tk.Label(lf, text="مسار الترجمة المحدد:", bg=C["card"], fg=C["text_dim"],
                 font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=(8, 4))

        stream_row = ttk.Frame(lf)
        stream_row.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 4))
        stream_row.columnconfigure(0, weight=1)

        self.stream_combo = ttk.Combobox(stream_row, state="readonly")
        self.stream_combo.grid(row=0, column=0, sticky="ew", padx=(8, 8))
        self.stream_combo.bind("<<ComboboxSelected>>", self._on_stream_selected)

        ttk.Button(stream_row, text="🔍 إعادة كشف", command=self._detect_streams, width=12).grid(row=0, column=1, sticky="e")

        self.streams_status_label = tk.Label(lf, text="اختر ملفاً لكشف مسارات الترجمة تلقائياً", bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 9))
        self.streams_status_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="اختر ملف الترجمة",
            filetypes=[
                ("Subtitle / Video", "*.mks *.mkv *.sub"),
                ("MKS / MKV",        "*.mks *.mkv"),
                ("SubRip / VobSub",  "*.sub"),
                ("All Files",        "*.*"),
            ],
        )
        if path:
            self.input_var.set(path)
            self._detect_streams()

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title="اختر مجلد الحفظ")
        if folder:
            self.output_var.set(folder)
            self.config["output_dir"] = folder

    def _detect_streams(self) -> None:
        inp = self.input_var.get().strip()
        if not inp:
            self.streams_status_label.config(text="حدد ملف الترجمة أولاً", fg=C["yellow"])
            return

        self.streams_status_label.config(text="⠋ جاري تحليل مسارات الترجمة وتحديد النوع...", fg=C["accent2"])

        def _task():
            try:
                streams = MediaEngine().probe_subtitle_streams(inp)
                if streams:
                    combo_items = []
                    mapping = {}
                    info_dict = {}
                    recommended_combo_idx = 0
                    first_graphic_found = False

                    for i, s in enumerate(streams):
                        codec = s.get("codec_name", "unknown").upper()
                        lang = s.get("tags", {}).get("language", "und").upper()
                        title = s.get("tags", {}).get("title", "")
                        info_dict[i] = s

                        is_pgs = ("PGS" in codec or "HDMV" in codec)
                        is_vob = ("VOB" in codec or "DVD" in codec)

                        if is_pgs:
                            label = f"[{i}]  ⭐️ HDMV PGS  —  {lang}  (صورية - تحتاج OCR)"
                            if not first_graphic_found:
                                recommended_combo_idx = i
                                first_graphic_found = True
                        elif is_vob:
                            label = f"[{i}]  ⭐️ VobSub Graphic  —  {lang}  (صورية - تحتاج OCR)"
                            if not first_graphic_found:
                                recommended_combo_idx = i
                                first_graphic_found = True
                        elif "SUBRIP" in codec or "SRT" in codec:
                            label = f"[{i}]  ⚡ SubRip SRT Text  —  {lang}  (نصية - استخراج فوري)"
                        elif "ASS" in codec or "SSA" in codec:
                            label = f"[{i}]  ⚡ ASS Styled Text  —  {lang}  (نصية - استخراج فوري)"
                        else:
                            label = f"[{i}]  {codec}  —  {lang}"

                        if title:
                            label += f" ({title})"

                        combo_items.append(label)
                        mapping[i] = i

                    self._q.put(("streams_detected", combo_items, mapping, info_dict, recommended_combo_idx, len(streams)))
                    self._q.put(("log", "INFO", f"✔ تم كشف {len(streams)} مسار ترجمة وتحديد النوع تلقائياً."))
                else:
                    self._q.put(("streams_detected", [], {}, {}, 0, 0))
                    self._q.put(("log", "WARNING", "لم يتم العثور على مسارات ترجمة في هذا الملف"))
            except Exception as exc:
                self._q.put(("log", "ERROR", f"خطأ فحص المسارات: {exc}"))
                self._q.put(("streams_detected", [], {}, {}, 0, -1))

        threading.Thread(target=_task, daemon=True).start()

    def _on_stream_selected(self, event=None) -> None:
        idx = self.stream_combo.current()
        if idx in self.stream_mapping:
            self.selected_stream_idx = self.stream_mapping[idx]
            self.config["stream_idx"] = str(self.selected_stream_idx)
            self._save_config()

    def _build_engine_card(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="  🤖  محرك الذكاء الاصطناعي المحلي  ", padding=14)
        lf.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        lf.columnconfigure(1, weight=1)

        tk.Label(lf, text="نوع الخادم المحلي:", bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=4)

        current_url = self.config.get("lmstudio_url", "http://localhost:1234/v1")
        default_type = "🤖 LM Studio (Port 1234)" if "1234" in current_url else ("🦙 Ollama (Port 11434)" if "11434" in current_url else "🌐 رابط مخصص (Custom)")

        self.engine_type_var = tk.StringVar(value=default_type)
        self.engine_type_combo = ttk.Combobox(lf, textvariable=self.engine_type_var, values=["🤖 LM Studio (Port 1234)", "🦙 Ollama (Port 11434)", "🌐 رابط مخصص (Custom)"], state="readonly")
        self.engine_type_combo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        self.engine_type_combo.bind("<<ComboboxSelected>>", self._on_engine_type_changed)

        tk.Label(lf, text="رابط الخادم (API Base URL):", bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=4)
        self.lms_url_var = tk.StringVar(value=current_url)
        ttk.Entry(lf, textvariable=self.lms_url_var).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        tk.Label(lf, text="اسم الموديل المطلوب:", bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=4)
        self.model_var = tk.StringVar(value=self.config.get("lmstudio_model", "qwen/qwen2.5-vl-7b"))
        model_entry = ttk.Entry(lf, textvariable=self.model_var)
        model_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        model_entry.bind("<KeyRelease>", lambda e: self._save_config())

        btn_row = ttk.Frame(lf)
        btn_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        btn_row.columnconfigure(1, weight=1)

        ttk.Button(btn_row, text="⚡ تشغيل واختبار الموديل", command=self._check_model_status, width=20).grid(row=0, column=0, sticky="w")

        self.model_status_label = tk.Label(btn_row, textvariable=self.model_status_var, bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 9, "bold"), anchor="w")
        self.model_status_label.grid(row=0, column=1, sticky="ew", padx=(10, 0))

    def _on_engine_type_changed(self, event=None) -> None:
        sel = self.engine_type_var.get()
        if "LM Studio" in sel:
            self.lms_url_var.set("http://localhost:1234/v1")
        elif "Ollama" in sel:
            self.lms_url_var.set("http://localhost:11434/v1")
        self._save_config()

    def _build_controls_and_progress(self, parent: ttk.Frame) -> None:
        cf = ttk.LabelFrame(parent, text="  ⚙️  لوحة المعالجة والتقدم  ", padding=14)
        cf.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        cf.columnconfigure(2, weight=1)

        self.start_btn = ttk.Button(cf, text="▶  ابدأ المعالجة", style="Accent.TButton", command=self._toggle_start_pause)
        self.start_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.stop_btn = ttk.Button(cf, text="⏹  إيقاف", style="Stop.TButton", command=self._stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="w", padx=(0, 16))

        prog_container = ttk.Frame(cf)
        prog_container.grid(row=0, column=2, sticky="ew")
        prog_container.columnconfigure(0, weight=1)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(prog_container, variable=self.progress_var, maximum=100, style="TProgressbar")
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.progress_label = tk.Label(prog_container, text="0 / 0  (0%)", bg=C["card"], fg=C["accent2"], font=("Segoe UI", 9, "bold"), width=14)
        self.progress_label.grid(row=0, column=1, sticky="e")

        self.status_label = tk.Label(cf, text="جاهز لبدء تفريغ الترجمة", bg=C["card"], fg=C["text_dim"], font=("Segoe UI", 10))
        self.status_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_log(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="  📋  سجل العمليات | Log  ", padding=8)
        lf.grid(row=3, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(1, weight=1)

        tb = ttk.Frame(lf)
        tb.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        tb.columnconfigure(0, weight=1)

        chk = ttk.Checkbutton(tb, text="التمرير التلقائي", variable=self.autoscroll_var, style="TCheckbutton")
        chk.grid(row=0, column=0, sticky="w")

        ttk.Button(tb, text="📋 نسخ السجل", command=self._copy_log, width=12).grid(row=0, column=1, sticky="e", padx=(0, 6))
        ttk.Button(tb, text="🧹 مسح", command=self._clear_log, width=8).grid(row=0, column=2, sticky="e")

        self.log_text = tk.Text(
            lf,
            bg=C["log_bg"], fg=C["text"], insertbackground=C["accent"],
            font=("Segoe UI", 10), state="disabled",
            wrap="word", relief="flat", selectbackground=C["accent"]
        )
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)

        self.log_text.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")

        self.log_text.tag_config("INFO",    foreground=C["green"])
        self.log_text.tag_config("WARNING", foreground=C["yellow"])
        self.log_text.tag_config("ERROR",   foreground=C["red"])
        self.log_text.tag_config("DEBUG",   foreground=C["text_dim"])
        self.log_text.tag_config("HEADER",  foreground=C["accent2"])

    def _copy_log(self) -> None:
        try:
            content = self.log_text.get("1.0", "end-1c")
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self._log("INFO", "✔ تم نسخ سجل العمليات كاملاً إلى الحافظة.")
        except Exception as exc:
            self._log("ERROR", f"فشل نسخ السجل: {exc}")

    def _clear_log(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _get_ai_config(self) -> dict:
        return {
            "engine":   "lmstudio",
            "base_url": self.lms_url_var.get().strip(),
            "api_key":  "lm-studio",
            "model":    self.model_var.get().strip(),
        }

    def _check_model_status(self) -> None:
        self.model_status_var.set("⠋ جاري كشف الموديلات المحملة...")
        self.model_status_label.config(fg=C["accent2"])
        ai_cfg = self._get_ai_config()

        def _task():
            try:
                ac = AIClient(ai_cfg)
                ok, real_name_or_err, available_models, is_vision = ac.check_model()
                if ok:
                    vision_tag = "👁️ Vision Input" if is_vision else "📝 Text Only"
                    status_text = f"✔ جاهز [{vision_tag}]: {real_name_or_err}"
                    self._q.put(("model_status", True, status_text, real_name_or_err, available_models, is_vision))
                else:
                    status_text = f"❌ غير متاح: {real_name_or_err}"
                    self._q.put(("model_status", False, status_text, None, available_models, False))
            except Exception as exc:
                status_text = f"❌ خطأ: {exc}"
                self._q.put(("model_status", False, status_text, None, [], False))

        threading.Thread(target=_task, daemon=True).start()

    def _toggle_start_pause(self) -> None:
        if self._is_running:
            if self._pause_event.is_set():
                self._pause_event.clear()
                self.start_btn.config(text="▶   استئناف", style="Accent.TButton")
                self.status_label.config(text="⏸ متوقف مؤقتاً", fg=C["yellow"])
                self._q.put(("log", "WARNING", "⏸ تم إيقاف المعالجة مؤقتاً."))
            else:
                self._pause_event.set()
                self.start_btn.config(text="⏸   توقف مؤقت", style="Pause.TButton")
                self.status_label.config(text="جاري تفريغ الترجمة …", fg=C["accent2"])
                self._q.put(("log", "INFO", "▶ تم استئناف المعالجة."))
        else:
            self._start()

    def _start(self) -> None:
        inp = self.input_var.get().strip()
        out = self.output_var.get().strip()

        if not inp:
            messagebox.showwarning("تنبيه", "اختر ملف الترجمة أولاً")
            return
        if not out:
            messagebox.showwarning("تنبيه", "حدد مجلد الحفظ أولاً")
            return

        stream_idx = self.selected_stream_idx
        export_format = self.export_fmt_var.get()

        stream_meta = self.stream_info_dict.get(stream_idx, {})
        codec = stream_meta.get("codec_name", "").lower()
        is_text_stream = any(t in codec for t in ("subrip", "srt", "ass", "ssa", "text", "mov_text", "webvtt"))

        if not is_text_stream:
            ai_cfg = self._get_ai_config()
            try:
                ac = AIClient(ai_cfg)
                ok, real_name_or_err, _, _ = ac.check_model()
                if not ok:
                    messagebox.showwarning(
                        "تنبيه — تعذر الاتصال بالخادم",
                        f"تعذر الاتصال بخادم الذكاء الاصطناعي ({ai_cfg['base_url']}).\n\n"
                        f"تفاصيل الخطأ: {real_name_or_err}\n\n"
                        "يرجى التأكد من تشغيل برنامج LM Studio أو Ollama ثم الضغط على [⚡ تشغيل واختبار الموديل] وإعادة المحاولة."
                    )
                    self.status_label.config(text="❌ تعذر الاتصال بالخادم — يرجى تشغيل LM Studio أو Ollama", fg=C["red"])
                    return
            except Exception as exc:
                messagebox.showwarning(
                    "تنبيه — تعذر الاتصال بالخادم",
                    f"تعذر الاتصال بخادم الذكاء الاصطناعي: {exc}\n\n"
                    "يرجى التأكد من تشغيل برنامج LM Studio أو Ollama وإعادة المحاولة."
                )
                self.status_label.config(text="❌ تعذر الاتصال بالخادم — يرجى تشغيل LM Studio أو Ollama", fg=C["red"])
                return

        self._save_config()
        self._stop.clear()
        self._pause_event.set()
        self._is_running = True

        self.progress_bar.config(style="TProgressbar")
        self.progress_var.set(0)
        self.progress_label.config(text="0 / 0  (0%)")
        self.start_btn.config(text="⏸   توقف مؤقت", style="Pause.TButton")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="جاري المعالجة …", fg=C["accent2"])

        ai_cfg = self._get_ai_config()

        self._worker_thread = threading.Thread(
            target=self._worker,
            args=(inp, out, stream_idx, export_format, ai_cfg),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop(self) -> None:
        if self._is_running:
            ans = messagebox.askyesno(
                "تأكيد الإيقاف",
                "هل أنت تأكد من إيقاف عملية المعالجة الجارية؟\n\nسيتم حفظ التقدم الحالي في ملف الكاش لاستئنافه لاحقاً.",
                icon="warning"
            )
            if not ans:
                return

        self._stop.set()
        self._pause_event.set()
        self._q.put(("log", "WARNING", "⏹ جاري إيقاف المعالجة …"))

    def _worker(self, input_file: str, output_dir: str,
                stream_idx: int, export_format: str, ai_cfg: dict) -> None:

        me = MediaEngine()
        ip = ImageProcessor()
        ac = AIClient(ai_cfg)
        sb = SRTBuilder()

        stream_meta = self.stream_info_dict.get(stream_idx, {})
        codec = stream_meta.get("codec_name", "").lower()
        is_text_stream = any(t in codec for t in ("subrip", "srt", "ass", "ssa", "text", "mov_text", "webvtt"))

        if is_text_stream:
            try:
                self._q.put(("log", "INFO", f"⚡ مسار نصي مكتشف ({codec.upper()}) — جاري الاستخراج الفوري..."))
                self._q.put(("status", "استخراج فوري للمسار النصي..."))

                fmt_ext = export_format.lower().strip(".")
                stem = Path(input_file).stem
                out_path = os.path.join(output_dir, f"{stem}.{fmt_ext}")
                os.makedirs(output_dir, exist_ok=True)

                me.extract_text_sub(input_file, stream_idx, out_path)
                self._q.put(("progress", 1, 1))
                self._q.put(("log", "INFO", f"✅ تم استخراج الترجمة النصية فورياً: {out_path}"))
                self._q.put(("done", True))
                return
            except Exception as exc:
                self._q.put(("log", "WARNING", f"فشل الاستخراج المباشر، جاري الانتقال للنموذج العادي: {exc}"))

        user_selected_model = self.model_var.get().strip() or ac.get_real_model_name()
        ac.model = user_selected_model
        self._q.put(("log", "INFO", f"🤖 الموديل المستخدم: {user_selected_model}"))

        ext = Path(input_file).suffix.lower()

        if ext == ".sub":
            self._worker_sub(input_file, output_dir, export_format, me, ip, ac, sb)
        else:
            self._worker_pgs(input_file, output_dir, stream_idx, export_format, me, ip, ac, sb)

    def _save_session_cache(self, output_dir: str, stem: str, current_frame: int, sb) -> None:
        try:
            cache_dir = Path(output_dir) / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            cache_file = cache_dir / f"{stem}_session.json"
            data = {
                "stem": stem,
                "last_frame": current_frame,
                "entries": sb._entries,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            draft_srt = cache_dir / f"{stem}_draft.srt"
            sb.write_srt(str(draft_srt))
        except Exception as exc:
            logger.debug("Cache save failed: %s", exc)

    def _load_session_cache(self, output_dir: str, stem: str, sb) -> int:
        try:
            cache_file = Path(output_dir) / ".cache" / f"{stem}_session.json"
            if cache_file.exists():
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)

                entries = data.get("entries", [])
                for start, end, text in entries:
                    sb.add_entry(start, end, text)
                last_frame = data.get("last_frame", 0)
                self._q.put(("log", "INFO", f"📦 تم تحميل كاش الجلسة السابقة ({len(entries)} سطر). الاستئناف عند الإطار [{last_frame}]"))
                return last_frame
        except Exception as exc:
            logger.debug("Cache load failed: %s", exc)
        return 0

    def _clear_session_cache(self, output_dir: str, stem: str) -> None:
        try:
            cache_dir = Path(output_dir) / ".cache"
            for fn in (f"{stem}_session.json", f"{stem}_draft.srt"):
                p = cache_dir / fn
                if p.exists():
                    p.unlink()
        except Exception:
            pass

    def _worker_pgs(self, input_file: str, output_dir: str, stream_idx: int,
                    export_format: str, me, ip, ac, sb) -> None:
        temp_dir = tempfile.mkdtemp(prefix="subai_")
        sup_path = os.path.join(temp_dir, "subtitle.sup")
        stem = Path(input_file).stem

        try:
            self._q.put(("status", "استخراج مسار PGS …"))
            self._q.put(("log", "INFO", f"ملف الإدخال: {Path(input_file).name}  |  مسار: {stream_idx}"))

            me.extract_sup(input_file, stream_idx, sup_path)
            self._q.put(("log", "INFO", "✔ تم استخراج ملف .sup بنجاح"))

            self._q.put(("status", "قراءة إطارات الترجمة …"))
            frames = list(me.parse_sup(sup_path))

            if not frames:
                self._q.put(("log", "ERROR", "⚠ لم أجد أي إطار ترجمة — هل المسار صحيح؟"))
                self._q.put(("done", False))
                return

            total = len(frames)
            self._q.put(("log", "INFO", f"✔ وجدت {total} إطار ترجمة"))
            self._q.put(("total", total))

            start_frame_idx = self._load_session_cache(output_dir, stem, sb)

            for i, (start_ms, end_ms, pil_img) in enumerate(frames):
                if i < start_frame_idx:
                    continue

                if self._stop.is_set():
                    self._save_session_cache(output_dir, stem, i, sb)
                    self._q.put(("log", "WARNING", f"تم الإيقاف وحفظ الكاش عند الإطار {i + 1} / {total}"))
                    break

                if not self._pause_event.is_set():
                    self._save_session_cache(output_dir, stem, i, sb)
                    self._q.put(("log", "INFO", f"💾 تم حفظ الجلسة في الكاش (.cache) عند الإطار [{i + 1}]"))
                    while not self._pause_event.is_set() and not self._stop.is_set():
                        time.sleep(0.2)

                if self._stop.is_set():
                    self._save_session_cache(output_dir, stem, i, sb)
                    break

                self._q.put(("progress", i + 1, total))
                self._q.put(("status", f"معالجة الإطار  {i + 1} / {total}"))

                img_bytes = ip.prepare(pil_img)
                if img_bytes is None:
                    self._q.put(("log", "DEBUG", f"[{i+1:04d}]  إطار فارغ — تم تخطيه"))
                    continue

                text = ac.ocr(img_bytes)
                if not text:
                    self._q.put(("log", "DEBUG", f"[{i+1:04d}]  لا نص مقروء"))
                    continue

                preview = text[:60].replace("\n", " ")
                self._q.put(("log", "DEBUG", f"[{i+1:04d}]  {start_ms}ms → {end_ms}ms  |  {preview}"))
                sb.add_entry(start_ms, end_ms, text)

                if (i + 1) % 25 == 0:
                    self._save_session_cache(output_dir, stem, i + 1, sb)

            self._finish_write(input_file, output_dir, export_format, sb)

        except Exception as exc:
            import traceback
            self._q.put(("log", "ERROR", f"خطأ غير متوقع: {exc}"))
            self._q.put(("log", "ERROR", traceback.format_exc()))
            self._q.put(("done", False))

        finally:
            me.cleanup(temp_dir)

    def _worker_sub(self, input_file: str, output_dir: str, export_format: str,
                    me, ip, ac, sb) -> None:
        stem = Path(input_file).stem
        try:
            sub_type, gen = me.parse_sub(input_file)
            self._q.put(("log", "INFO", f"نوع الملف: {sub_type.upper()}  |  {Path(input_file).name}"))

            if sub_type == "vobsub":
                self._q.put(("status", "تحليل VobSub …"))
                frames = list(gen)
                if not frames:
                    self._q.put(("log", "ERROR", "⚠ لم أجد إطارات في ملف VobSub"))
                    self._q.put(("done", False))
                    return

                total = len(frames)
                self._q.put(("log", "INFO", f"✔ {total} صورة VobSub"))
                self._q.put(("total", total))

                start_frame_idx = self._load_session_cache(output_dir, stem, sb)

                for i, (start_ms, end_ms, pil_img) in enumerate(frames):
                    if i < start_frame_idx:
                        continue

                    if self._stop.is_set():
                        self._save_session_cache(output_dir, stem, i, sb)
                        self._q.put(("log", "WARNING", f"تم الإيقاف وحفظ الكاش عند {i + 1} / {total}"))
                        break

                    if not self._pause_event.is_set():
                        self._save_session_cache(output_dir, stem, i, sb)
                        self._q.put(("log", "INFO", f"💾 تم حفظ الجلسة في الكاش (.cache) عند الإطار [{i + 1}]"))
                        while not self._pause_event.is_set() and not self._stop.is_set():
                            time.sleep(0.2)

                    if self._stop.is_set():
                        self._save_session_cache(output_dir, stem, i, sb)
                        break

                    self._q.put(("progress", i + 1, total))
                    self._q.put(("status", f"معالجة VobSub  {i + 1} / {total}"))

                    img_bytes = ip.prepare_vobsub(pil_img)
                    if img_bytes is None:
                        self._q.put(("log", "DEBUG", f"[{i+1:04d}]  صورة فارغة — تخطي"))
                        continue

                    text = ac.ocr(img_bytes)
                    if not text:
                        self._q.put(("log", "DEBUG", f"[{i+1:04d}]  لا نص مقروء"))
                        continue

                    preview = text[:60].replace("\n", " ")
                    self._q.put(("log", "DEBUG", f"[{i+1:04d}]  {start_ms}ms → {end_ms}ms  |  {preview}"))
                    sb.add_entry(start_ms, end_ms, text)

                    if (i + 1) % 25 == 0:
                        self._save_session_cache(output_dir, stem, i + 1, sb)

            else:
                self._q.put(("status", f"تحليل {sub_type} …"))
                entries = list(gen)
                if not entries:
                    self._q.put(("log", "ERROR", f"⚠ لم أجد ترجمات في ملف {sub_type}"))
                    self._q.put(("done", False))
                    return

                total = len(entries)
                self._q.put(("log", "INFO", f"✔ {total} سطر ترجمة ({sub_type}) — لا يحتاج OCR"))
                self._q.put(("total", total))

                for i, (start_ms, end_ms, text) in enumerate(entries):
                    if self._stop.is_set():
                        break

                    while not self._pause_event.is_set() and not self._stop.is_set():
                        time.sleep(0.2)

                    if self._stop.is_set():
                        break

                    self._q.put(("progress", i + 1, total))
                    sb.add_entry(start_ms, end_ms, text)

            self._finish_write(input_file, output_dir, export_format, sb)

        except Exception as exc:
            import traceback
            self._q.put(("log", "ERROR", f"خطأ: {exc}"))
            self._q.put(("log", "ERROR", traceback.format_exc()))
            self._q.put(("done", False))

    def _finish_write(self, input_file: str, output_dir: str, export_format: str, sb) -> None:
        os.makedirs(output_dir, exist_ok=True)
        stem = Path(input_file).stem
        fmt_ext = export_format.lower().strip(".")
        out_path = os.path.join(output_dir, f"{stem}.{fmt_ext}")

        sb.write_export(out_path, format_ext=fmt_ext)
        count = sb.count()
        self._clear_session_cache(output_dir, stem)
        self._q.put(("log", "INFO", f"✅ تم الحفظ بنجاح بصيغة ({export_format.upper()}): {out_path}"))
        self._q.put(("log", "INFO", f"   إجمالي أسطر الترجمة: {count}"))
        self._q.put(("done", True))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self._q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "log":
            _, level, text = msg
            self._log(level, text)
        elif kind == "status":
            self.status_label.config(text=msg[1])
        elif kind == "progress":
            _, cur, total = msg
            pct = int((cur / total) * 100) if total > 0 else 0
            self.progress_var.set(pct)
            self.progress_label.config(text=f"{cur} / {total}  ({pct}%)")
        elif kind == "streams_detected":
            _, items, mapping, info_dict, rec_idx, total_count = msg
            self.stream_mapping = mapping
            self.stream_info_dict = info_dict
            self.stream_combo["values"] = items

            if items:
                self.stream_combo.current(rec_idx)
                self.selected_stream_idx = mapping.get(rec_idx, 0)
                self.config["stream_idx"] = str(self.selected_stream_idx)
                self._save_config()
                self.streams_status_label.config(
                    text=f"✔ تم كشف {total_count} مسار ترجمة • المحدد تلقائياً: المسار [{self.selected_stream_idx}]",
                    fg=C["green"]
                )
            elif total_count == 0:
                self.streams_status_label.config(text="⚠ لم يتم العثور على مسارات ترجمة", fg=C["yellow"])
            else:
                self.streams_status_label.config(text="❌ فشل كشف المسارات عبر FFprobe", fg=C["red"])

        elif kind == "model_status":
            success = msg[1]
            text = msg[2]
            real_name = msg[3] if len(msg) > 3 else None
            available_models = msg[4] if len(msg) > 4 else []
            is_vision = msg[5] if len(msg) > 5 else False

            self.model_status_var.set(text)
            self.model_status_label.config(fg=C["green"] if (success and is_vision) else (C["yellow"] if success else C["red"]))
            self._log("INFO" if success else "WARNING", f"حالة الموديل: {text}")

            if success:
                self.config["lmstudio_model"] = self.model_var.get().strip()
                self._save_config()
        elif kind == "total":
            pass
        elif kind == "done":
            success = msg[1]
            self._is_running = False
            self._pause_event.set()
            self.start_btn.config(text="▶  ابدأ المعالجة", style="Accent.TButton", state="normal")
            self.stop_btn.config(state="disabled")
            if success:
                self.progress_bar.config(style="Success.TProgressbar")
                self.progress_var.set(100)
                self.status_label.config(text="✅ اكتملت المعالجة وحفظ الترجمة بنجاح", fg=C["green"])
            else:
                self.status_label.config(text="⚠ اكتملت العملية مع وجود تنبيهات أو أخطاء", fg=C["yellow"])

    def _log(self, level: str, text: str) -> None:
        self.log_text.config(state="normal")
        tag = level if level in ("INFO", "WARNING", "ERROR", "DEBUG") else "INFO"
        self.log_text.insert("end", f"[{level:7s}]  {text}\n", tag)
        if self.autoscroll_var.get():
            self.log_text.see("end")
        self.log_text.config(state="disabled")
        logger.log(
            {"INFO": 20, "WARNING": 30, "ERROR": 40, "DEBUG": 10}.get(level, 20),
            text,
        )


def main() -> None:
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = SubAIMasterPro(root)
    root.update_idletasks()
    w, h = 960, 760
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    root.mainloop()


if __name__ == "__main__":
    main()
