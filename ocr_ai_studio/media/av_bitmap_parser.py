from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


class AVBitmapError(RuntimeError):
    pass


class AVBitmapParser:
    """Decode FFmpeg bitmap subtitle codecs through PyAV without altering their PTS."""

    def parse(self, source: Path, stream_ordinal: int):
        try:
            import av
        except ImportError as exc:
            raise AVBitmapError(
                "يلزم تثبيت PyAV لفك DVB Subtitle وXSUB وDVD: python -m pip install av"
            ) from exc

        try:
            container = av.open(str(source))
        except Exception as exc:
            raise AVBitmapError(f"تعذر فتح مصدر الترجمة الصورية عبر PyAV: {exc}") from exc
        with container:
            streams = list(container.streams.subtitles)
            if not 0 <= stream_ordinal < len(streams):
                raise AVBitmapError("مسار الترجمة الصورية المحدد غير موجود")
            stream = streams[stream_ordinal]
            codec_name = str(stream.codec_context.name).casefold()
            pending: tuple[int, int, Image.Image] | None = None
            for packet in container.demux(stream):
                try:
                    decoded_sets = packet.decode()
                except Exception as exc:
                    raise AVBitmapError(f"فشل فك حزمة ترجمة صورية: {exc}") from exc
                if decoded_sets and not hasattr(decoded_sets[0], "rects"):
                    decoded_sets = [decoded_sets]
                for subtitle_set in decoded_sets:
                    rectangles = (
                        subtitle_set
                        if isinstance(subtitle_set, list)
                        else subtitle_set.rects
                    )
                    image = self._compose_rectangles(rectangles)
                    if image is None:
                        continue
                    if isinstance(subtitle_set, list):
                        xsub_timing = self._xsub_timing(bytes(packet)) if codec_name == "xsub" else None
                        if xsub_timing is not None:
                            start_ms, end_ms = xsub_timing
                        else:
                            start_ms = self._packet_start_ms(packet)
                            packet_duration = (
                                round(float(packet.duration * packet.time_base) * 1_000)
                                if packet.duration and packet.time_base
                                else 5_000
                            )
                            end_ms = start_ms + max(1, packet_duration)
                    else:
                        start_ms = self._start_ms(subtitle_set, packet)
                        end_ms = start_ms + max(
                            1,
                            int(subtitle_set.end_display_time)
                            - int(subtitle_set.start_display_time),
                        )
                    if pending is not None:
                        old_start, old_end, old_image = pending
                        adjusted_end = min(old_end, start_ms) if start_ms > old_start else old_end
                        yield old_start, adjusted_end, old_image
                    pending = (start_ms, end_ms, image)
            if pending is not None:
                yield pending

    def count(self, source: Path, stream_ordinal: int) -> int:
        return sum(1 for _frame in self.parse(source, stream_ordinal))

    @staticmethod
    def _start_ms(subtitle_set: Any, packet: Any) -> int:
        # AVSubtitle.pts uses AV_TIME_BASE (microseconds), independent of stream time_base.
        pts = getattr(subtitle_set, "pts", None)
        if pts is not None:
            base_ms = round(int(pts) / 1_000)
        elif packet.pts is not None and packet.time_base is not None:
            base_ms = round(float(packet.pts * packet.time_base) * 1_000)
        else:
            raise AVBitmapError("حزمة الترجمة لا تحتوي على توقيت PTS صالح")
        return max(0, base_ms + int(subtitle_set.start_display_time))

    @staticmethod
    def _packet_start_ms(packet: Any) -> int:
        if packet.pts is None or packet.time_base is None:
            raise AVBitmapError("حزمة الترجمة لا تحتوي على توقيت PTS صالح")
        return max(0, round(float(packet.pts * packet.time_base) * 1_000))

    @staticmethod
    def _xsub_timing(packet_data: bytes) -> tuple[int, int] | None:
        match = re.match(
            rb"\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})-(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]",
            packet_data,
        )
        if match is None:
            return None
        values = [int(value) for value in match.groups()]

        def milliseconds(parts: list[int]) -> int:
            hours, minutes, seconds, millis = parts
            return hours * 3_600_000 + minutes * 60_000 + seconds * 1_000 + millis

        start_ms = milliseconds(values[:4])
        end_ms = milliseconds(values[4:])
        return (start_ms, end_ms) if end_ms > start_ms else None

    def _compose_rectangles(self, rectangles: list[Any]) -> Image.Image | None:
        bitmap_rects = [
            rect
            for rect in rectangles
            if "bitmap" in str(getattr(rect, "type", "")).casefold()
        ]
        if not bitmap_rects:
            return None
        left = min(int(rect.x) for rect in bitmap_rects)
        top = min(int(rect.y) for rect in bitmap_rects)
        right = max(int(rect.x) + int(rect.width) for rect in bitmap_rects)
        bottom = max(int(rect.y) + int(rect.height) for rect in bitmap_rects)
        canvas = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
        for rect in bitmap_rects:
            image = self._rectangle_image(rect)
            canvas.alpha_composite(image, (int(rect.x) - left, int(rect.y) - top))
        return canvas

    @staticmethod
    def _rectangle_image(rect: Any) -> Image.Image:
        if not rect.planes:
            raise AVBitmapError("صورة الترجمة لا تحتوي على بيانات بكسلات")
        width, height = int(rect.width), int(rect.height)
        indexes = bytes(rect.planes[0])[: width * height]
        if len(indexes) != width * height:
            raise AVBitmapError("حجم بيانات صورة الترجمة غير مكتمل")
        if len(rect.planes) == 1:
            counts = Counter(indexes)
            background_indexes = {0}
            nonzero_background = next(
                (index for index, _count in counts.most_common() if index != 0),
                None,
            )
            if nonzero_background is not None:
                background_indexes.add(nonzero_background)
            pixels = [
                (0, 0, 0, 0) if index in background_indexes else (255, 255, 255, 255)
                for index in indexes
            ]
            image = Image.new("RGBA", (width, height))
            image.putdata(pixels)
            return image

        palette_data = bytes(rect.planes[1])
        colors: list[tuple[int, int, int, int]] = []
        for offset in range(0, min(len(palette_data), int(rect.nb_colors) * 4), 4):
            value = int.from_bytes(palette_data[offset : offset + 4], sys.byteorder)
            colors.append(
                (
                    (value >> 16) & 0xFF,
                    (value >> 8) & 0xFF,
                    value & 0xFF,
                    (value >> 24) & 0xFF,
                )
            )
        if not colors:
            raise AVBitmapError("لوحة ألوان الترجمة فارغة")
        pixels = [colors[index] if index < len(colors) else (0, 0, 0, 0) for index in indexes]
        image = Image.new("RGBA", (width, height))
        image.putdata(pixels)
        return image
