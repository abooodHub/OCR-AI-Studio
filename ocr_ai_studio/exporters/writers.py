from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from ocr_ai_studio.domain.models import SubtitleCue


def merge_adjacent_cues(cues: Iterable[SubtitleCue], max_gap_ms: int = 250) -> list[SubtitleCue]:
    ordered = sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms))
    if not ordered:
        return []
    merged = [ordered[0]]
    for cue in ordered[1:]:
        previous = merged[-1]
        same_text = cue.text.strip() == previous.text.strip()
        close_enough = 0 <= cue.start_ms - previous.end_ms <= max_gap_ms
        if same_text and close_enough:
            merged[-1] = SubtitleCue(
                start_ms=previous.start_ms,
                end_ms=max(previous.end_ms, cue.end_ms),
                text=previous.text,
                confidence=previous.confidence,
                frame_index=previous.frame_index,
            )
        else:
            merged.append(cue)
    return merged


class SubtitleExporter:
    def export(self, cues: Iterable[SubtitleCue], destination: Path, format_name: str) -> None:
        fmt = format_name.lower().lstrip(".")
        writers = {
            "srt": self._srt,
            "vtt": self._vtt,
            "ass": self._ass,
            "txt": self._txt,
        }
        if fmt not in writers:
            raise ValueError(f"Unsupported export format: {format_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = writers[fmt](merge_adjacent_cues(cues))
        handle, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temp_path = Path(temporary)
            if temp_path.exists():
                temp_path.unlink()

    def _srt(self, cues: list[SubtitleCue]) -> str:
        blocks = []
        for index, cue in enumerate(cues, 1):
            blocks.append(
                f"{index}\n{self._timestamp(cue.start_ms, ',')} --> "
                f"{self._timestamp(cue.end_ms, ',')}\n{cue.text}"
            )
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def _vtt(self, cues: list[SubtitleCue]) -> str:
        blocks = ["WEBVTT"]
        for cue in cues:
            blocks.append(
                f"{self._timestamp(cue.start_ms, '.')} --> {self._timestamp(cue.end_ms, '.')}\n{cue.text}"
            )
        return "\n\n".join(blocks) + "\n"

    def _ass(self, cues: list[SubtitleCue]) -> str:
        header = (
            "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nPlayResX: 1920\nPlayResY: 1080\n\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,"
            "0,0,1,2,0,2,40,40,30,1\n\n[Events]\n"
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )
        lines = [header]
        for cue in cues:
            text = cue.text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
            text = text.replace("\n", r"\N")
            lines.append(
                f"Dialogue: 0,{self._ass_timestamp(cue.start_ms)},{self._ass_timestamp(cue.end_ms)},"
                f"Default,,0,0,0,,{text}\n"
            )
        return "".join(lines)

    @staticmethod
    def _txt(cues: list[SubtitleCue]) -> str:
        return "\n".join(cue.text for cue in cues) + ("\n" if cues else "")

    @staticmethod
    def _timestamp(milliseconds: int, separator: str) -> str:
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"

    @staticmethod
    def _ass_timestamp(milliseconds: int) -> str:
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours}:{minutes:02}:{seconds:02}.{millis // 10:02}"
