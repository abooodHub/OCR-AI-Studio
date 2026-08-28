from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from ocr_ai_studio.domain.models import StreamInfo


class FFmpegError(RuntimeError):
    pass


class FFmpegService:
    def __init__(
        self,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        mkvextract: str = "mkvextract",
        mkvmerge: str = "mkvmerge",
    ) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.mkvextract = mkvextract
        self.mkvmerge = mkvmerge

    @staticmethod
    def _find_executable(command: str) -> str | None:
        detected = shutil.which(command)
        if detected:
            return detected
        if os.name == "nt":
            executable = command if command.lower().endswith(".exe") else f"{command}.exe"
            candidate = Path("C:/Program Files/MKVToolNix") / executable
            if candidate.is_file():
                return str(candidate)
        return None

    def diagnostics(self) -> dict[str, str | bool]:
        ffmpeg_path = self._find_executable(self.ffmpeg)
        ffprobe_path = self._find_executable(self.ffprobe)
        mkvextract_path = self._find_executable(self.mkvextract)
        mkvmerge_path = self._find_executable(self.mkvmerge)
        return {
            "ffmpeg_available": bool(ffmpeg_path),
            "ffprobe_available": bool(ffprobe_path),
            "ffmpeg_path": ffmpeg_path or "",
            "ffprobe_path": ffprobe_path or "",
            "mkvextract_available": bool(mkvextract_path),
            "mkvextract_path": mkvextract_path or "",
            "mkvmerge_available": bool(mkvmerge_path),
            "mkvmerge_path": mkvmerge_path or "",
        }

    def probe_subtitles(self, source: Path, *, dvd_title: int = 1) -> list[StreamInfo]:
        if not source.exists():
            raise FFmpegError(f"Input file does not exist: {source}")
        if source.is_dir():
            dvd_root = self._dvd_root(source)
            command = [
                self.ffprobe,
                "-v",
                "error",
                "-f",
                "dvdvideo",
                "-title",
                str(dvd_title),
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index,codec_name:stream_tags=language,title",
                "-of",
                "json",
                str(dvd_root),
            ]
            return self._subtitle_streams_from_probe(command, dvd_title=dvd_title)
        suffix = source.suffix.lower()
        if suffix in {".sup", ".pgs"}:
            return [StreamInfo(0, 0, "hdmv_pgs_subtitle", title="Standalone SUP")]
        if suffix == ".xml":
            from ocr_ai_studio.media.bdn_parser import BDNParser

            BDNParser(source)
            return [StreamInfo(0, 0, "bdn_xml", title="Blu-ray BDN XML")]
        if suffix == ".idx":
            return [StreamInfo(0, 0, "dvd_subtitle", title="Standalone VobSub")]
        if suffix == ".sub":
            from ocr_ai_studio.media.vobsub_parser import detect_sub_type

            sub_type = detect_sub_type(str(source))
            codec = "dvd_subtitle" if sub_type == "vobsub" else sub_type
            title = {
                "vobsub": "Standalone VobSub",
                "microdvd": "Standalone MicroDVD",
                "subviewer": "Standalone SubViewer",
            }.get(sub_type, "Standalone SUB")
            return [StreamInfo(0, 0, codec, title=title)]
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
        return self._subtitle_streams_from_probe(command)

    def _subtitle_streams_from_probe(
        self, command: list[str], *, dvd_title: int | None = None
    ) -> list[StreamInfo]:
        result = self._run(command, timeout=180 if dvd_title is not None else 45)
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
                    title=str(
                        tags.get("title")
                        or (f"DVD Title {dvd_title}" if dvd_title is not None else "")
                    ),
                )
            )
        return streams

    @staticmethod
    def _dvd_root(source: Path) -> Path:
        candidate = source / "VIDEO_TS" if (source / "VIDEO_TS").is_dir() else source
        if not (candidate / "VIDEO_TS.IFO").is_file():
            raise FFmpegError("المجلد المحدد لا يحتوي على بنية DVD صالحة (VIDEO_TS.IFO)")
        return candidate

    def extract_dvd_title(
        self,
        source: Path,
        stream_ordinal: int,
        destination: Path,
        *,
        title: int = 1,
    ) -> None:
        dvd_root = self._dvd_root(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "dvdvideo",
            "-preindex",
            "1",
            "-title",
            str(title),
            "-i",
            str(dvd_root),
            "-map",
            f"0:s:{stream_ordinal}",
            "-c:s",
            "copy",
            str(destination),
        ]
        self._run(command, timeout=3_600)
        if not destination.is_file() or destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            raise FFmpegError("استخراج ترجمة DVD لم ينتج حاوية صورية صالحة")

    def extract_pgs(self, source: Path, stream_ordinal: int, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_timestamps = self._probe_subtitle_timestamp_bounds(source, stream_ordinal)
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-copyts",
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
        extracted_timestamps = self._sup_timestamp_bounds(destination)
        if extracted_timestamps is None:
            destination.unlink(missing_ok=True)
            raise FFmpegError("PGS extraction did not contain valid timestamped segments")
        if source_timestamps is not None and any(
            abs(source_ms - extracted_ms) > 5
            for source_ms, extracted_ms in zip(source_timestamps, extracted_timestamps, strict=True)
        ):
            destination.unlink(missing_ok=True)
            raise FFmpegError(
                "فشل التحقق من توقيت PGS؛ أُوقفت المهمة قبل بدء OCR لمنع إنتاج ترجمة "
                f"غير متزامنة (المصدر {source_timestamps}، الناتج {extracted_timestamps})"
            )

    def extract_vobsub(self, source: Path, stream_ordinal: int, destination_idx: Path) -> None:
        """Extract an embedded DVD subtitle stream as a timestamped IDX/SUB pair."""
        if source.suffix.lower() not in {".mkv", ".mks", ".webm"}:
            raise FFmpegError(
                "الاستخراج الآمن لمسار VobSub المضمّن متاح حاليًا لحاويات MKV/MKS فقط"
            )
        destination_idx = destination_idx.with_suffix(".idx")
        destination_sub = destination_idx.with_suffix(".sub")
        destination_idx.parent.mkdir(parents=True, exist_ok=True)
        source_timestamps = self._probe_subtitle_timestamp_bounds(source, stream_ordinal)
        mkvextract_path = self._find_executable(self.mkvextract)
        mkvmerge_path = self._find_executable(self.mkvmerge)
        if not mkvextract_path or not mkvmerge_path:
            raise FFmpegError(
                "يتطلب استخراج VobSub المضمّن تثبيت MKVToolNix (mkvmerge وmkvextract)"
            )
        identify = self._run([mkvmerge_path, "-J", str(source)], timeout=120)
        try:
            payload = json.loads(identify.stdout or "{}")
            subtitle_tracks = [
                track for track in payload.get("tracks", []) if track.get("type") == "subtitles"
            ]
            track_id = int(subtitle_tracks[stream_ordinal]["id"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FFmpegError("تعذر مطابقة مسار الترجمة مع معرّف MKVToolNix") from exc
        self._run(
            [mkvextract_path, "tracks", str(source), f"{track_id}:{destination_idx}"],
            timeout=600,
        )
        if (
            not destination_idx.exists()
            or destination_idx.stat().st_size == 0
            or not destination_sub.exists()
            or destination_sub.stat().st_size == 0
        ):
            self._remove_vobsub_pair(destination_idx)
            raise FFmpegError("VobSub extraction did not produce a valid IDX/SUB pair")
        extracted_timestamps = self._idx_timestamp_bounds(destination_idx)
        if extracted_timestamps is None:
            self._remove_vobsub_pair(destination_idx)
            raise FFmpegError("VobSub extraction did not contain timestamp entries")
        if source_timestamps is not None and any(
            abs(source_ms - extracted_ms) > 15
            for source_ms, extracted_ms in zip(source_timestamps, extracted_timestamps, strict=True)
        ):
            first_delta = source_timestamps[0] - extracted_timestamps[0]
            last_delta = source_timestamps[1] - extracted_timestamps[1]
            if abs(first_delta - last_delta) <= 15 and extracted_timestamps[0] + first_delta >= 0:
                self._shift_idx_timestamps(destination_idx, first_delta)
                extracted_timestamps = self._idx_timestamp_bounds(destination_idx)
            if extracted_timestamps is None or any(
                abs(source_ms - extracted_ms) > 15
                for source_ms, extracted_ms in zip(
                    source_timestamps, extracted_timestamps, strict=True
                )
            ):
                self._remove_vobsub_pair(destination_idx)
                raise FFmpegError(
                    "فشل التحقق من توقيت VobSub؛ أُوقفت المهمة قبل بدء OCR لمنع إنتاج ترجمة "
                    f"غير متزامنة (المصدر {source_timestamps}، الناتج {extracted_timestamps})"
                )

    @staticmethod
    def _remove_vobsub_pair(idx_path: Path) -> None:
        idx_path.unlink(missing_ok=True)
        idx_path.with_suffix(".sub").unlink(missing_ok=True)

    @staticmethod
    def _idx_timestamps(source: Path) -> list[int]:
        content = source.read_text(encoding="utf-8-sig", errors="replace")
        timestamps = []
        pattern = re.compile(r"timestamp:\s*(\d+):(\d+):(\d+):(\d+)", re.IGNORECASE)
        for match in pattern.finditer(content):
            hours, minutes, seconds, millis = (int(value) for value in match.groups())
            if minutes >= 60 or seconds >= 60:
                continue
            timestamps.append(
                hours * 3_600_000 + minutes * 60_000 + seconds * 1_000 + millis
            )
        return timestamps

    @classmethod
    def _shift_idx_timestamps(cls, source: Path, offset_ms: int) -> None:
        content = source.read_text(encoding="utf-8-sig", errors="replace")
        pattern = re.compile(r"(timestamp:\s*)(\d+):(\d+):(\d+):(\d+)", re.IGNORECASE)

        def replace(match: re.Match[str]) -> str:
            current = (
                int(match.group(2)) * 3_600_000
                + int(match.group(3)) * 60_000
                + int(match.group(4)) * 1_000
                + int(match.group(5))
            )
            shifted = current + offset_ms
            if shifted < 0:
                raise FFmpegError("VobSub timestamp correction would produce a negative value")
            hours, remainder = divmod(shifted, 3_600_000)
            minutes, remainder = divmod(remainder, 60_000)
            seconds, millis = divmod(remainder, 1_000)
            return f"{match.group(1)}{hours:02}:{minutes:02}:{seconds:02}:{millis:03}"

        source.write_text(pattern.sub(replace, content), encoding="utf-8", newline="\n")

    @classmethod
    def _idx_timestamp_bounds(cls, source: Path) -> tuple[int, int] | None:
        timestamps = cls._idx_timestamps(source)
        return (timestamps[0], timestamps[-1]) if timestamps else None

    @classmethod
    def count_vobsub_frames(cls, source: Path) -> int:
        idx_path = source if source.suffix.lower() == ".idx" else source.with_suffix(".idx")
        return len(cls._idx_timestamps(idx_path)) if idx_path.exists() else 0

    def _probe_subtitle_timestamp_bounds(
        self,
        source: Path,
        stream_ordinal: int,
    ) -> tuple[int, int] | None:
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-select_streams",
            f"s:{stream_ordinal}",
            "-show_packets",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "json",
            str(source),
        ]
        result = self._run(command, timeout=120)
        try:
            payload = json.loads(result.stdout or "{}")
            timestamps = [
                round(float(packet["pts_time"]) * 1_000)
                for packet in payload.get("packets", [])
                if packet.get("pts_time") not in {None, "N/A"}
            ]
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FFmpegError("FFprobe returned invalid PGS packet timestamps") from exc
        return (timestamps[0], timestamps[-1]) if timestamps else None

    @staticmethod
    def _sup_timestamp_bounds(source: Path) -> tuple[int, int] | None:
        data = source.read_bytes()
        timestamps: list[int] = []
        offset = 0
        while offset + 13 <= len(data):
            if data[offset : offset + 2] != b"PG":
                next_segment = data.find(b"PG", offset + 1)
                if next_segment < 0:
                    break
                offset = next_segment
                continue
            pts = struct.unpack(">I", data[offset + 2 : offset + 6])[0]
            segment_size = struct.unpack(">H", data[offset + 11 : offset + 13])[0]
            segment_end = offset + 13 + segment_size
            if segment_end > len(data):
                break
            timestamps.append(pts // 90)
            offset = segment_end
        return (timestamps[0], timestamps[-1]) if timestamps else None

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
