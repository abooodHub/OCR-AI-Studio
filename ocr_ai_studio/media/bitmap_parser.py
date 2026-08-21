"""
Bitmap subtitle parsers for PGS and VobSub sources.
يتعامل مع FFmpeg/FFprobe لاستخراج مسار PGS،
وفيه محلل PGS مدمج لتحويل البيانات إلى صور PIL + توقيتات.
يدعم أيضاً ملفات .sub بجميع أنواعها (VobSub / MicroDVD / SubViewer).
"""

import json
import os
import shutil
import struct
import subprocess

from PIL import Image

# ======================================================================
# Media Engine
# ======================================================================


class MediaEngine:
    """High-level interface: probe, extract, and parse PGS subtitles."""

    # ------------------------------------------------------------------
    # FFprobe
    # ------------------------------------------------------------------

    def probe_subtitle_streams(self, file_path: str) -> list[dict]:
        """Return a list of subtitle stream info dicts from ffprobe."""
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "s",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe error:\n{result.stderr}")
        data = json.loads(result.stdout or "{}")
        return data.get("streams", [])

    def get_duration_ms(self, file_path: str) -> int:
        """Return file duration in milliseconds (best effort)."""
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return 0
        data = json.loads(result.stdout or "{}")
        duration = float(data.get("format", {}).get("duration", 0))
        return int(duration * 1000)

    # ------------------------------------------------------------------
    # FFmpeg — extract PGS stream
    # ------------------------------------------------------------------

    def extract_sup(self, input_file: str, stream_idx: int, output_sup: str) -> None:
        """
        Extract the PGS subtitle stream at index `stream_idx`
        into a raw .sup (PGS) binary file using FFmpeg.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-map",
            f"0:s:{stream_idx}",
            "-c",
            "copy",
            output_sup,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg خطأ في الاستخراج:\n{result.stderr}")
        if not os.path.exists(output_sup) or os.path.getsize(output_sup) == 0:
            raise RuntimeError("ملف .sup فارغ أو لم يُنشأ — تأكد أن المسار يحتوي على PGS")

    def extract_text_sub(self, input_file: str, stream_idx: int, output_path: str) -> None:
        """
        Instantly extract a text-based subtitle stream (SubRip, ASS, VTT, etc.)
        direct to file without needing Vision OCR.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-map",
            f"0:s:{stream_idx}",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error during text subtitle extraction:\n{result.stderr}")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("استخراج المسار النصي فشل أو أخرج ملفاً فارغاً")

    # ------------------------------------------------------------------
    # PGS parser
    # ------------------------------------------------------------------

    def parse_sup(self, sup_path: str):
        """
        Parse a .sup PGS file.
        Yields (start_ms, end_ms, PIL.Image.Image) for each subtitle.
        """
        parser = PGSParser(sup_path)
        yield from parser.parse()

    # ------------------------------------------------------------------
    # .sub file support
    # ------------------------------------------------------------------

    def detect_sub_type(self, sub_path: str) -> str:
        """
        Detect the type of a .sub file.
        Returns: 'vobsub' | 'microdvd' | 'subviewer' | 'unknown'
        """
        try:
            from .vobsub_parser import detect_sub_type
        except ImportError:
            from ocr_ai_studio.media.vobsub_parser import detect_sub_type
        return detect_sub_type(sub_path)

    def parse_sub(self, sub_path: str, fps: float | None = None):
        """
        Parse a .sub file (any supported type).

        Returns a tuple (sub_type, generator) where:
          - 'vobsub'   → generator yields (start_ms, end_ms, PIL.Image)
          - 'microdvd' → generator yields (start_ms, end_ms, str)
          - 'subviewer'→ generator yields (start_ms, end_ms, str)
        """
        try:
            from .vobsub_parser import parse_sub_file
        except ImportError:
            from ocr_ai_studio.media.vobsub_parser import parse_sub_file
        return parse_sub_file(sub_path, fps=fps)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self, temp_dir: str) -> None:
        """Remove the temporary directory."""
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ======================================================================
# PGS Parser — sيستخرج الصور والتوقيتات مباشرة من ملف .sup
# ======================================================================


class PGSParser:
    """
    Lightweight PGS (Presentation Graphic Stream) parser.
    No external libraries required beyond Pillow.

    PGS segment types:
      0x14  PDS  Palette Definition Segment
      0x15  ODS  Object Definition Segment
      0x16  PCS  Presentation Composition Segment
      0x17  WDS  Window Definition Segment
      0x80  END  End of Display Set
    """

    # Segment type constants
    SEG_PDS = 0x14
    SEG_ODS = 0x15
    SEG_PCS = 0x16
    SEG_WDS = 0x17
    SEG_END = 0x80

    # ODS sequence flags
    ODS_FIRST = 0x80
    ODS_LAST = 0x40

    def __init__(self, sup_path: str) -> None:
        self.sup_path = sup_path

    # ------------------------------------------------------------------
    # Top-level entry point
    # ------------------------------------------------------------------

    def parse(self):
        """Yield (start_ms, end_ms, PIL.Image) for each subtitle event."""
        with open(self.sup_path, "rb") as fh:
            raw = fh.read()

        segments = self._read_segments(raw)
        display_sets = self._group_display_sets(segments)
        yield from self._render_all(display_sets)

    # ------------------------------------------------------------------
    # 1. Read raw segments
    # ------------------------------------------------------------------

    def _read_segments(self, data: bytes) -> list[dict]:
        segments = []
        offset = 0
        length = len(data)

        while offset + 13 <= length:
            # Sync on magic bytes "PG"
            if data[offset : offset + 2] != b"PG":
                next_pg = data.find(b"PG", offset + 1)
                if next_pg == -1:
                    break
                offset = next_pg
                continue

            pts_raw = struct.unpack(">I", data[offset + 2 : offset + 6])[0]
            seg_type = data[offset + 10]
            seg_size = struct.unpack(">H", data[offset + 11 : offset + 13])[0]
            end = offset + 13 + seg_size

            if end > length:
                break

            segments.append(
                {
                    "pts_ms": pts_raw // 90,  # 90 kHz → ms
                    "type": seg_type,
                    "data": data[offset + 13 : end],
                }
            )
            offset = end

        return segments

    # ------------------------------------------------------------------
    # 2. Group into display sets
    # ------------------------------------------------------------------

    def _group_display_sets(self, segments: list[dict]) -> list[list[dict]]:
        display_sets, current = [], []
        for seg in segments:
            current.append(seg)
            if seg["type"] == self.SEG_END:
                display_sets.append(current)
                current = []
        if current:
            display_sets.append(current)
        return display_sets

    # ------------------------------------------------------------------
    # 3. Render display sets → images
    # ------------------------------------------------------------------

    def _render_all(self, display_sets: list[list[dict]]):
        """
        Walk display sets, maintain running palette/object state,
        and yield (start_ms, end_ms, img) pairs.
        """
        palettes: dict[int, dict[int, tuple]] = {}
        objects: dict[int, dict] = {}
        object_bufs: dict[int, dict] = {}  # accumulate multi-seg ODS

        pending: tuple | None = None  # (start_ms, palette_id, comp_objs, objs_snap, pals_snap)

        for ds in display_sets:
            pcs = None
            ds_palettes: dict[int, dict] = {}
            ds_objects: dict[int, dict] = {}

            for seg in ds:
                t = seg["type"]
                if t == self.SEG_PCS:
                    pcs = self._parse_pcs(seg)
                elif t == self.SEG_PDS:
                    pid, pal = self._parse_pds(seg)
                    ds_palettes[pid] = pal
                elif t == self.SEG_ODS:
                    oid, obj = self._parse_ods(seg, object_bufs)
                    if obj is not None:
                        ds_objects[oid] = obj

            if pcs is None:
                continue

            # Update running state BEFORE snapshotting
            palettes.update(ds_palettes)
            objects.update(ds_objects)

            comp_objs = pcs.get("composition_objects", [])
            pts_ms = pcs["pts_ms"]

            if not comp_objs:
                # --- END event ---
                if pending is not None:
                    s_ms, p_id, c_objs, o_snap, pal_snap = pending
                    img = self._render_image(c_objs, o_snap, pal_snap, p_id)
                    if img is not None:
                        yield (s_ms, pts_ms, img)
                    pending = None
            else:
                # --- START event ---
                palette_id = pcs.get("palette_id", 0)
                pending = (
                    pts_ms,
                    palette_id,
                    comp_objs,
                    dict(objects),  # shallow copy (values are immutable)
                    dict(palettes),
                )

        # Handle unclosed final display set
        if pending is not None:
            s_ms, p_id, c_objs, o_snap, pal_snap = pending
            img = self._render_image(c_objs, o_snap, pal_snap, p_id)
            if img is not None:
                yield (s_ms, s_ms + 3000, img)

    # ------------------------------------------------------------------
    # Segment parsers
    # ------------------------------------------------------------------

    def _parse_pcs(self, seg: dict) -> dict:
        """Parse Presentation Composition Segment."""
        d = seg["data"]
        if len(d) < 11:
            return {"pts_ms": seg["pts_ms"], "composition_objects": []}

        palette_id = d[9]
        num_comp_objs = d[10]

        comp_objects = []
        offset = 11
        for _ in range(num_comp_objs):
            if offset + 8 > len(d):
                break
            obj_id = struct.unpack(">H", d[offset : offset + 2])[0]
            obj_cropped = d[offset + 3]
            x = struct.unpack(">H", d[offset + 4 : offset + 6])[0]
            y = struct.unpack(">H", d[offset + 6 : offset + 8])[0]
            offset += 8

            if obj_cropped & 0x40:  # cropped flag
                offset += 8  # skip 4 crop fields × 2 bytes

            comp_objects.append({"object_id": obj_id, "x": x, "y": y})

        return {
            "pts_ms": seg["pts_ms"],
            "palette_id": palette_id,
            "composition_objects": comp_objects,
        }

    def _parse_pds(self, seg: dict) -> tuple[int, dict[int, tuple]]:
        """Parse Palette Definition Segment → (palette_id, {index: (R,G,B,A)})."""
        d = seg["data"]
        if len(d) < 2:
            return 0, {}

        palette_id = d[0]
        palette: dict[int, tuple] = {}
        offset = 2

        while offset + 4 < len(d):
            idx = d[offset]
            Y = d[offset + 1]
            Cb = d[offset + 2]
            Cr = d[offset + 3]
            alpha = d[offset + 4]

            # BT.601 YCbCr → RGB
            Y_f = Y - 16
            Cb_f = Cb - 128
            Cr_f = Cr - 128
            r = int(max(0, min(255, 1.164 * Y_f + 1.596 * Cr_f)))
            g = int(max(0, min(255, 1.164 * Y_f - 0.392 * Cb_f - 0.813 * Cr_f)))
            b = int(max(0, min(255, 1.164 * Y_f + 2.017 * Cb_f)))

            palette[idx] = (r, g, b, alpha)
            offset += 5

        return palette_id, palette

    def _parse_ods(self, seg: dict, bufs: dict) -> tuple[int, dict | None]:
        """
        Parse Object Definition Segment.
        Handles multi-segment objects (streams with flag 0x80/0x40).
        Returns (object_id, object_dict | None).
        object_dict = {'width': int, 'height': int, 'rle_data': bytes}
        """
        d = seg["data"]
        if len(d) < 4:
            return 0, None

        obj_id = struct.unpack(">H", d[0:2])[0]
        sequence_flag = d[3]

        is_first = bool(sequence_flag & self.ODS_FIRST)
        is_last = bool(sequence_flag & self.ODS_LAST)

        if is_first:
            if len(d) < 11:
                return obj_id, None
            width = struct.unpack(">H", d[7:9])[0]
            height = struct.unpack(">H", d[9:11])[0]
            rle = bytearray(d[11:])
            bufs[obj_id] = {"width": width, "height": height, "rle_data": rle}
        else:
            rle = d[4:]
            if obj_id in bufs:
                bufs[obj_id]["rle_data"].extend(rle)

        if is_last and obj_id in bufs:
            buf = bufs[obj_id]
            return obj_id, {
                "width": buf["width"],
                "height": buf["height"],
                "rle_data": bytes(buf["rle_data"]),
            }

        return obj_id, None

    # ------------------------------------------------------------------
    # RLE decoder
    # ------------------------------------------------------------------

    def _decode_rle(self, rle: bytes, width: int, height: int) -> list[int]:
        """
        Decode PGS run-length encoding to a flat list of palette indices.

        After a 0x00 marker:
          0x00        → end of line
          0x01–0x3F  → run of N transparent (color 0) pixels
          0x40–0x7F  → run of (byte&0x3F)<<8 | next_byte transparent pixels
          0x80–0xBF  → run of (byte&0x3F) pixels of next_byte color
          0xC0–0xFF  → run of (byte&0x3F)<<8|next_byte pixels of next_byte2 color
        """
        pixels: list[int] = []
        i = 0
        n = len(rle)

        while i < n:
            b = rle[i]
            i += 1

            if b != 0:
                pixels.append(b)
                continue

            if i >= n:
                break
            b2 = rle[i]
            i += 1

            if b2 == 0:
                # End of line — no explicit action needed
                pass
            elif b2 < 0x40:
                pixels.extend([0] * b2)
            elif b2 < 0x80:
                if i >= n:
                    break
                b3 = rle[i]
                i += 1
                count = ((b2 & 0x3F) << 8) | b3
                pixels.extend([0] * count)
            elif b2 < 0xC0:
                count = b2 & 0x3F
                if i >= n:
                    break
                color = rle[i]
                i += 1
                pixels.extend([color] * count)
            else:
                if i + 1 >= n:
                    break
                b3 = rle[i]
                i += 1
                color = rle[i]
                i += 1
                count = ((b2 & 0x3F) << 8) | b3
                pixels.extend([color] * count)

        return pixels

    # ------------------------------------------------------------------
    # Image renderer
    # ------------------------------------------------------------------

    def _render_image(
        self,
        comp_objects: list[dict],
        objects: dict[int, dict],
        palettes: dict[int, dict],
        palette_id: int,
    ) -> Image.Image | None:
        """Compose composition objects into a single RGBA PIL image."""
        if not comp_objects or not objects or not palettes:
            return None

        pal = palettes.get(palette_id) or (next(iter(palettes.values())) if palettes else None)
        if not pal:
            return None

        rendered: list[tuple[int, int, Image.Image]] = []

        for comp_obj in comp_objects:
            oid = comp_obj["object_id"]
            obj = objects.get(oid)
            if obj is None:
                continue

            w, h = obj["width"], obj["height"]
            if w == 0 or h == 0:
                continue

            pixels = self._decode_rle(obj["rle_data"], w, h)

            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            pixel_data = img.load()
            limit = min(len(pixels), w * h)
            for idx in range(limit):
                rgba = pal.get(pixels[idx], (0, 0, 0, 0))
                pixel_data[idx % w, idx // w] = rgba

            rendered.append((comp_obj["x"], comp_obj["y"], img))

        if not rendered:
            return None

        if len(rendered) == 1:
            return rendered[0][2]

        # Multiple objects → composite onto canvas
        max_x = max(x + img.width for x, y, img in rendered)
        max_y = max(y + img.height for x, y, img in rendered)
        canvas = Image.new("RGBA", (max_x, max_y), (0, 0, 0, 0))
        for x, y, img in rendered:
            canvas.alpha_composite(img, (x, y))
        return canvas
