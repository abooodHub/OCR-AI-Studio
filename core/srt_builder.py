"""
srt_builder.py — صانع ومصدّر ملفات الترجمة لصيغ متعددة
يدعم التصدير إلى (.srt, .vtt, .ass, .txt) مع تجميع التكرار تلقائياً.
"""

from pathlib import Path


class SRTBuilder:
    """Collects subtitle entries, deduplicates, and writes subtitle files in various formats."""

    def __init__(self):
        self._entries: list[tuple[int, int, str]] = []  # (start_ms, end_ms, text)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_entry(self, start_ms: int, end_ms: int, text: str) -> None:
        """Append a raw subtitle entry."""
        text = text.strip()
        if not text:
            return
        self._entries.append((start_ms, end_ms, text))

    def count(self) -> int:
        """Number of entries after deduplication."""
        return len(self._deduplicate())

    def write_export(self, output_path: str, format_ext: str = "srt") -> None:
        """Export to specified format extension ('srt', 'vtt', 'ass', 'txt')."""
        fmt = format_ext.lower().lstrip(".")
        if fmt == "vtt":
            self.write_vtt(output_path)
        elif fmt == "ass":
            self.write_ass(output_path)
        elif fmt == "txt":
            self.write_txt(output_path)
        else:
            self.write_srt(output_path)

    def write(self, output_path: str) -> None:
        """Default export (SRT)."""
        self.write_srt(output_path)

    def write_srt(self, output_path: str) -> None:
        """Write SubRip (.srt) file."""
        entries = self._deduplicate()
        with open(output_path, "w", encoding="utf-8") as f:
            for idx, (start, end, text) in enumerate(entries, 1):
                f.write(f"{idx}\n")
                f.write(f"{self._fmt_ms_srt(start)} --> {self._fmt_ms_srt(end)}\n")
                f.write(f"{text}\n\n")

    def write_vtt(self, output_path: str) -> None:
        """Write WebVTT (.vtt) file."""
        entries = self._deduplicate()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n")
            for idx, (start, end, text) in enumerate(entries, 1):
                f.write(f"{idx}\n")
                f.write(f"{self._fmt_ms_vtt(start)} --> {self._fmt_ms_vtt(end)}\n")
                f.write(f"{text}\n\n")

    def write_ass(self, output_path: str) -> None:
        """Write Advanced SubStation Alpha (.ass) file."""
        entries = self._deduplicate()
        header = (
            "[Script Info]\n"
            "Title: SubAI Export\n"
            "ScriptType: v4.00+\n"
            "WrapStyle: 0\n"
            "PlayResX: 1920\n"
            "PlayResY: 1080\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header)
            for start, end, text in entries:
                clean_text = text.replace("\n", r"\N")
                f.write(f"Dialogue: 0,{self._fmt_ms_ass(start)},{self._fmt_ms_ass(end)},Default,,0,0,0,,{clean_text}\n")

    def write_txt(self, output_path: str) -> None:
        """Write plain text (.txt) file without timestamps."""
        entries = self._deduplicate()
        with open(output_path, "w", encoding="utf-8") as f:
            for _, _, text in entries:
                f.write(f"{text}\n")

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _deduplicate(self) -> list[tuple[int, int, str]]:
        if not self._entries:
            return []

        merged: list[tuple[int, int, str]] = []
        start_ms, end_ms, text = self._entries[0]

        for s, e, t in self._entries[1:]:
            if t.strip() == text.strip():
                end_ms = e
            else:
                merged.append((start_ms, end_ms, text))
                start_ms, end_ms, text = s, e, t

        merged.append((start_ms, end_ms, text))
        return merged

    # ------------------------------------------------------------------
    # Timestamp Formatters
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_ms_srt(ms: int) -> str:
        """Format ms → HH:MM:SS,mmm"""
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1_000
        ms %= 1_000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _fmt_ms_vtt(ms: int) -> str:
        """Format ms → HH:MM:SS.mmm"""
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1_000
        ms %= 1_000
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    @staticmethod
    def _fmt_ms_ass(ms: int) -> str:
        """Format ms → H:MM:SS.cs (centiseconds)"""
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1_000
        cs = (ms % 1_000) // 10
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
