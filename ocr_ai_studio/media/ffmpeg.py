from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ocr_ai_studio.domain.models import StreamInfo


class FFmpegError(RuntimeError):
    pass


class FFmpegService:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def diagnostics(self) -> dict[str, str | bool]:
        ffmpeg_path = shutil.which(self.ffmpeg)
        ffprobe_path = shutil.which(self.ffprobe)
        return {
            "ffmpeg_available": bool(ffmpeg_path),
            "ffprobe_available": bool(ffprobe_path),
            "ffmpeg_path": ffmpeg_path or "",
            "ffprobe_path": ffprobe_path or "",
        }

    def probe_subtitles(self, source: Path) -> list[StreamInfo]:
        if not source.is_file():
            raise FFmpegError(f"Input file does not exist: {source}")
        suffix = source.suffix.lower()
        if suffix == ".sup":
            return [StreamInfo(0, 0, "hdmv_pgs_subtitle", title="Standalone SUP")]
        if suffix in {".sub", ".idx"}:
            return [StreamInfo(0, 0, "dvd_subtitle", title="Standalone VobSub")]
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index,codec_name:stream_tags=language,title",
            "-of",
            "json",
            str(source),
        ]
        result = self._run(command, timeout=45)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise FFmpegError("FFprobe returned invalid JSON") from exc

        streams = []
        for ordinal, item in enumerate(payload.get("streams", [])):
            tags = item.get("tags") or {}
            streams.append(
                StreamInfo(
                    ordinal=ordinal,
                    source_index=int(item.get("index", ordinal)),
                    codec=str(item.get("codec_name") or "unknown"),
                    language=str(tags.get("language") or "und"),
                    title=str(tags.get("title") or ""),
                )
            )
        return streams

    def extract_pgs(self, source: Path, stream_ordinal: int, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:s:{stream_ordinal}",
            "-c:s",
            "copy",
            str(destination),
        ]
        self._run(command, timeout=600)
        if not destination.exists() or destination.stat().st_size == 0:
            raise FFmpegError("PGS extraction produced an empty file")

    def extract_text(self, source: Path, stream_ordinal: int, destination: Path, export_format: str) -> None:
        codec_by_format = {"srt": "srt", "vtt": "webvtt", "ass": "ass"}
        destination.parent.mkdir(parents=True, exist_ok=True)
        requested_format = export_format.lower()
        temporary_suffix = ".srt" if requested_format == "txt" else f".{requested_format}"
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-", suffix=temporary_suffix, dir=destination.parent
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:s:{stream_ordinal}",
        ]
        codec = codec_by_format.get("srt" if requested_format == "txt" else requested_format)
        if codec:
            command.extend(["-c:s", codec])
        command.append(str(temporary_path))
        try:
            self._run(command, timeout=300)
            if not temporary_path.exists() or temporary_path.stat().st_size == 0:
                raise FFmpegError("Text subtitle extraction produced an empty file")
            if requested_format == "txt":
                srt_content = temporary_path.read_text(encoding="utf-8-sig", errors="replace")
                destination.write_text(self._srt_to_text(srt_content), encoding="utf-8", newline="\n")
                temporary_path.unlink()
            else:
                os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _srt_to_text(content: str) -> str:
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.isdigit() or re.match(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->", stripped):
                continue
            lines.append(stripped)
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise FFmpegError(f"Required executable was not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(f"Command timed out after {timeout} seconds") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Unknown FFmpeg error").strip()
            raise FFmpegError(detail)
        return result
