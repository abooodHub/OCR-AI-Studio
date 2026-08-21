"""
Parsers for VobSub, MicroDVD, and SubViewer subtitle sources.
يدعم:
  • VobSub  (.sub + .idx)  — ترجمة صور bitmap تحتاج OCR
  • MicroDVD (.sub)         — ترجمة نصية بأرقام إطارات
  • SubViewer (.sub)        — ترجمة نصية بتوقيتات زمنية
"""

import re
import struct
from collections.abc import Generator
from pathlib import Path

from PIL import Image

# ============================================================
# Auto-detector: يحدد نوع ملف .sub
# ============================================================


def detect_sub_type(sub_path: str) -> str:
    """
    Detect the type of a .sub file.
    Returns: 'vobsub' | 'microdvd' | 'subviewer' | 'unknown'

    Detection order:
      1. Binary check — MPEG-2 PS magic bytes 00 00 01 BA  → vobsub
      2. Companion .idx file exists                        → vobsub
      3. Text content — MicroDVD pattern {N}{N}           → microdvd
      4. Text content — SubViewer timestamp pattern       → subviewer
    """
    # ── 1. Binary magic bytes: MPEG-2 Program Stream (VobSub raw) ──────
    try:
        with open(sub_path, "rb") as fh:
            magic = fh.read(4)
        if magic == b"\x00\x00\x01\xba":
            return "vobsub"
    except Exception:
        pass

    # ── 2. Companion .idx file ──────────────────────────────────────────
    idx_path = Path(sub_path).with_suffix(".idx")
    if idx_path.exists():
        return "vobsub"

    # ── 3 & 4. Text-based formats ───────────────────────────────────────
    try:
        with open(sub_path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(2048)
    except Exception:
        return "unknown"

    if (
        head.startswith("[INFORMATION]")
        or "[END INFORMATION]" in head
        or re.search(r"\d{2}:\d{2}:\d{2}\.\d{2},\d{2}:\d{2}:\d{2}\.\d{2}", head)
    ):
        return "subviewer"

    if re.search(r"^\{\d+\}\{\d+\}", head, re.MULTILINE):
        return "microdvd"

    return "unknown"


# ============================================================
# VobSub Parser
# ============================================================


class VobSubParser:
    """
    Parses VobSub (.idx + .sub) subtitle files.
    Yields (start_ms, end_ms, PIL.Image.Image) for each subtitle.
    VobSub images need OCR since they are bitmap-based.
    """

    # MPEG-2 PS / private stream constants
    PS_HEADER = b"\x00\x00\x01\xba"
    PRIV_STREAM_1 = b"\x00\x00\x01\xbd"

    # DVD SPU control sequence commands (ECMA-267 / DVD spec)
    # Each control sequence block:  [delay:2][next_block_off:2][cmd...][0xFF]
    SPU_CMD_FORCE_DISPLAY = 0x00  # force display (show subtitle)
    SPU_CMD_STOP_DISPLAY = 0x01  # stop display
    SPU_CMD_PALETTE = 0x03  # 2 bytes: palette indices
    SPU_CMD_ALPHA = 0x04  # 2 bytes: alpha values
    SPU_CMD_COORDS = 0x05  # 6 bytes: x1,x2,y1,y2
    SPU_CMD_OFFSETS = 0x06  # 4 bytes: top/bottom field offsets
    SPU_CMD_END = 0xFF  # end of this control sequence block

    def __init__(self, sub_path: str) -> None:
        self.sub_path = sub_path
        self.idx_path = str(Path(sub_path).with_suffix(".idx"))

    # ----------------------------------------------------------
    # Public entry point
    # ----------------------------------------------------------

    def parse(self) -> Generator[tuple[int, int, Image.Image], None, None]:
        """Yield (start_ms, end_ms, PIL.Image) for each subtitle."""
        sub_data = self._read_sub()
        if not sub_data:
            return

        idx_exists = Path(self.idx_path).exists()

        # Load the global palette from .idx if available
        idx_palette = self._read_idx_palette() if idx_exists else None

        if idx_exists:
            # ── Fast path: use .idx for timestamps + offsets ──────────
            timestamps = self._read_idx()
            entries = self._extract_spu_packets(sub_data, timestamps)
        else:
            # ── Fallback: scan MPEG-2 PS stream for PTS + SPU data ────
            entries = self._scan_mpeg_stream(sub_data)

        for start_ms, end_ms, spu_data in entries:
            img = self._decode_spu(spu_data, idx_palette=idx_palette)
            if img is not None:
                yield (start_ms, end_ms, img)

    # ----------------------------------------------------------
    # MPEG-2 PS full scan (no .idx needed)
    # ----------------------------------------------------------

    def _scan_mpeg_stream(self, data: bytes) -> list[tuple[int, int, bytes]]:
        """
        Walk the raw MPEG-2 PS stream and collect every private_stream_1
        (0x000001BD) packet that carries a DVD SPU sub-stream (id 0x20-0x3F).
        Timestamps come from the PES PTS field.

        Returns list of (start_ms, end_ms, spu_bytes) sorted by start_ms.
        """
        results: list[tuple[int, int, bytes]] = []
        # spu_id → (pts_ms, accumulated_bytes)
        pending: dict[int, tuple[int, bytearray]] = {}

        i = 0
        n = len(data)

        while i + 6 <= n:
            # Skip pack headers (00 00 01 BA)
            if data[i : i + 4] == b"\x00\x00\x01\xba":
                # Pack header: 14 bytes fixed + optional stuffing
                if i + 14 > n:
                    break
                stuffing = data[i + 13] & 0x07
                i += 14 + stuffing
                continue

            # Private stream 1 (00 00 01 BD)
            if data[i : i + 4] == b"\x00\x00\x01\xbd":
                if i + 6 > n:
                    break
                pkt_len = struct.unpack(">H", data[i + 4 : i + 6])[0]
                pkt_end = i + 6 + pkt_len
                if pkt_end > n:
                    break

                pts_ms = self._read_pts(data, i + 6)
                payload_s = i + 6 + self._pes_header_len(data, i + 6)

                if payload_s < pkt_end:
                    sub_id = data[payload_s]  # 0x20 = first sub stream
                    payload_s += 1  # skip stream_id byte

                    if 0x20 <= sub_id <= 0x3F and payload_s < pkt_end:
                        chunk = data[payload_s:pkt_end]
                        sid = sub_id & 0x1F  # logical track 0–31

                        if sid not in pending:
                            pending[sid] = (pts_ms, bytearray(chunk))
                        else:
                            pending[sid][1].extend(chunk)

                        # Check if we have the full SPU
                        acc = pending[sid][1]
                        if len(acc) >= 2:
                            spu_size = struct.unpack(">H", bytes(acc[:2]))[0]
                            if len(acc) >= spu_size:
                                s_ms = pending[sid][0]
                                spu = bytes(acc[:spu_size])
                                end_ms = s_ms + self._spu_duration_ms(spu)
                                results.append((s_ms, end_ms, spu))
                                del pending[sid]

                i = pkt_end
                continue

            i += 1  # advance byte by byte until next sync

        # Flush any incomplete trailing SPU packets
        for _sid, (s_ms, acc) in pending.items():
            if len(acc) >= 4:
                end_ms = s_ms + 3_000
                results.append((s_ms, end_ms, bytes(acc)))

        results.sort(key=lambda x: x[0])
        return results

    @staticmethod
    def _read_pts(data: bytes, pes_start: int) -> int:
        """Extract PTS from PES header (in ms). Returns 0 if unavailable."""
        if pes_start + 3 >= len(data):
            return 0
        flags = data[pes_start + 1] if pes_start + 1 < len(data) else 0
        if not (flags & 0x80):  # no PTS present
            return 0
        p = pes_start + 3
        if p + 5 > len(data):
            return 0
        pts = (
            ((data[p] & 0x0E) << 29)
            | (data[p + 1] << 22)
            | ((data[p + 2] & 0xFE) << 14)
            | (data[p + 3] << 7)
            | ((data[p + 4] & 0xFE) >> 1)
        )
        return pts // 90  # 90 kHz → ms

    @staticmethod
    def _pes_header_len(data: bytes, pes_start: int) -> int:
        """Return total PES header length (fixed 3 bytes + extension byte)."""
        if pes_start + 2 >= len(data):
            return 3
        return 3 + data[pes_start + 2]

    @staticmethod
    def _spu_duration_ms(spu: bytes) -> int:
        """
        Try to read the forced-display delay from the first SPU control command.
        Falls back to 3 000 ms if not found.
        """
        if len(spu) < 6:
            return 3_000
        ctrl_off = struct.unpack(">H", spu[2:4])[0]
        if ctrl_off + 2 >= len(spu):
            return 3_000
        delay_raw = struct.unpack(">H", spu[ctrl_off : ctrl_off + 2])[0]
        delay_ms = (delay_raw >> 1) * (1024 // 90)  # MPEG 1/90000 s units
        return max(delay_ms, 1_000)  # at least 1 second

    # ----------------------------------------------------------
    # IDX reader — timestamps + byte offsets
    # ----------------------------------------------------------

    def _read_idx(self) -> list[tuple[int, int]]:
        """
        Parse .idx file.
        Returns list of (timestamp_ms, byte_offset) sorted by offset.
        """
        timestamps: list[tuple[int, int]] = []
        try:
            with open(self.idx_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(
                        r"timestamp:\s*(\d{2}):(\d{2}):(\d{2}):(\d{3}),\s*filepos:\s*([0-9a-fA-F]+)",
                        line.strip(),
                    )
                    if m:
                        h, mn, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                        ts_ms = h * 3_600_000 + mn * 60_000 + s * 1_000 + ms
                        offset = int(m.group(5), 16)
                        timestamps.append((ts_ms, offset))
        except Exception:
            pass
        return sorted(timestamps, key=lambda x: x[1])

    def _read_idx_palette(self) -> list[tuple[int, int, int]] | None:
        """
        Parse the 'palette:' line from the .idx file.
        Returns a list of 16 (R, G, B) tuples, or None if not found.
        The palette is stored as 24-bit RGB hex values.
        """
        try:
            with open(self.idx_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("palette:"):
                        hex_colors = [c.strip() for c in line[8:].split(",")]
                        palette = []
                        for hc in hex_colors:
                            hc = hc.strip()
                            if len(hc) == 6:
                                r = int(hc[0:2], 16)
                                g = int(hc[2:4], 16)
                                b = int(hc[4:6], 16)
                                palette.append((r, g, b))
                        if len(palette) == 16:
                            return palette
        except Exception:
            pass
        return None

    # ----------------------------------------------------------
    # SUB reader — raw binary
    # ----------------------------------------------------------

    def _read_sub(self) -> bytes | None:
        try:
            with open(self.sub_path, "rb") as fh:
                return fh.read()
        except Exception:
            return None

    # ----------------------------------------------------------
    # Extract SPU packets at given byte offsets
    # ----------------------------------------------------------

    def _extract_spu_packets(
        self,
        data: bytes,
        timestamps: list[tuple[int, int]],
    ) -> list[tuple[int, int, bytes]]:
        """
        Extract raw SPU data from each timestamp offset.
        Returns list of (start_ms, end_ms, spu_bytes).
        """
        results = []
        n = len(timestamps)

        for i, (ts_ms, offset) in enumerate(timestamps):
            # Guess end_ms from next timestamp or +3 seconds
            if i + 1 < n:
                end_ms = timestamps[i + 1][0]
            else:
                end_ms = ts_ms + 3_000

            spu = self._read_spu_at(data, offset)
            if spu:
                results.append((ts_ms, end_ms, spu))

        return results

    def _read_spu_at(self, data: bytes, offset: int) -> bytes | None:
        """
        Collect all MPEG-2 PS payload bytes for one SPU starting at `offset`.
        A single SPU is split across consecutive pack+PES packet pairs.
        We keep collecting until we have spu_size bytes (first 2 bytes of SPU).
        """
        spu = bytearray()
        pos = offset
        n = len(data)

        while pos + 14 <= n:
            # ── Pack header (00 00 01 BA) ─────────────────────────────
            if data[pos : pos + 4] != self.PS_HEADER:
                break
            stuffing = data[pos + 13] & 0x07
            pos += 14 + stuffing

            # ── Private stream 1 (00 00 01 BD) ───────────────────────
            if pos + 6 > n or data[pos : pos + 4] != self.PRIV_STREAM_1:
                break

            pkt_len = struct.unpack(">H", data[pos + 4 : pos + 6])[0]
            pkt_end = pos + 6 + pkt_len
            if pkt_end > n:
                break

            # PES header: [flags_byte1][flags_byte2][hdr_len][...hdr_len bytes...]
            pes_start = pos + 6
            if pes_start + 3 > n:
                break
            hdr_len = data[pes_start + 2]
            payload_pos = pes_start + 3 + hdr_len

            # First byte of payload = sub-stream id (0x20 for track 0)
            payload_pos += 1

            if payload_pos < pkt_end:
                spu.extend(data[payload_pos:pkt_end])

            pos = pkt_end

            # Have we accumulated the full SPU?
            if len(spu) >= 2:
                spu_size = struct.unpack(">H", bytes(spu[:2]))[0]
                if spu_size > 0 and len(spu) >= spu_size:
                    return bytes(spu[:spu_size])

        return bytes(spu) if len(spu) >= 4 else None

    # ----------------------------------------------------------
    # SPU decoder → PIL image
    # ----------------------------------------------------------

    def _decode_spu(self, spu: bytes, idx_palette: list | None = None) -> Image.Image | None:
        """
        Decode a raw DVD SPU packet into a PIL RGBA image.

        SPU binary layout:
          [2] total_packet_size
          [2] control_sequence_offset   (from start of SPU)
          [N] pixel_data (RLE, two interlaced fields)

        Control sequence blocks (can be chained):
          [2] display_delay  (units: 1/90000s >> 1)
          [2] next_block_off (offset of next control block, or self = last)
          [cmd_bytes...]
          [0xFF]            (end of this block)

        DVD SPU command bytes:
          0x00  Force display
          0x01  Stop display
          0x03  Palette   — 2 bytes
          0x04  Alpha     — 2 bytes
          0x05  Coords    — 6 bytes
          0x06  PF offsets— 4 bytes (top field, bottom field)
          0xFF  End of block
        """
        if len(spu) < 4:
            return None

        ctrl_offset = struct.unpack(">H", spu[2:4])[0]
        if ctrl_offset >= len(spu):
            return None

        # ── Default values ────────────────────────────────────────────
        # palette indices into idx_palette (or fallback 4-color map)
        pal_idx = {0: 0, 1: 1, 2: 2, 3: 3}
        alpha_map = {0: 0, 1: 15, 2: 15, 3: 15}
        x1 = y1 = x2 = y2 = 0
        top_field_offset = 4  # pixel data starts right after SPU header
        bot_field_offset = 0
        found_coords = False

        # ── Walk control sequence blocks ──────────────────────────────
        block_pos = ctrl_offset
        visited = set()

        while block_pos < len(spu) - 3 and block_pos not in visited:
            visited.add(block_pos)

            # Each block: [delay:2][next_off:2][cmds...][0xFF]
            # delay and next_off — just read and advance
            if block_pos + 4 > len(spu):
                break
            next_block = struct.unpack(">H", spu[block_pos + 2 : block_pos + 4])[0]
            pos = block_pos + 4  # skip delay(2) + next_off(2)

            while pos < len(spu):
                cmd = spu[pos]
                pos += 1

                if cmd == self.SPU_CMD_END:
                    break

                elif cmd == self.SPU_CMD_FORCE_DISPLAY:  # 0x00 — no data
                    pass

                elif cmd == self.SPU_CMD_STOP_DISPLAY:  # 0x01 — no data
                    pass

                elif cmd == self.SPU_CMD_PALETTE:  # 0x03 — 2 bytes
                    if pos + 2 > len(spu):
                        break
                    b1 = spu[pos]
                    b2 = spu[pos + 1]
                    pos += 2
                    pal_idx[3] = (b1 >> 4) & 0xF
                    pal_idx[2] = b1 & 0xF
                    pal_idx[1] = (b2 >> 4) & 0xF
                    pal_idx[0] = b2 & 0xF

                elif cmd == self.SPU_CMD_ALPHA:  # 0x04 — 2 bytes
                    if pos + 2 > len(spu):
                        break
                    b1 = spu[pos]
                    b2 = spu[pos + 1]
                    pos += 2
                    alpha_map[3] = (b1 >> 4) & 0xF
                    alpha_map[2] = b1 & 0xF
                    alpha_map[1] = (b2 >> 4) & 0xF
                    alpha_map[0] = b2 & 0xF

                elif cmd == self.SPU_CMD_COORDS:  # 0x05 — 6 bytes
                    if pos + 6 > len(spu):
                        break
                    x1 = (spu[pos] << 4) | (spu[pos + 1] >> 4)
                    x2 = ((spu[pos + 1] & 0xF) << 8) | spu[pos + 2]
                    y1 = (spu[pos + 3] << 4) | (spu[pos + 4] >> 4)
                    y2 = ((spu[pos + 4] & 0xF) << 8) | spu[pos + 5]
                    pos += 6
                    found_coords = True

                elif cmd == self.SPU_CMD_OFFSETS:  # 0x06 — 4 bytes
                    if pos + 4 > len(spu):
                        break
                    top_field_offset = struct.unpack(">H", spu[pos : pos + 2])[0]
                    bot_field_offset = struct.unpack(">H", spu[pos + 2 : pos + 4])[0]
                    pos += 4

                else:
                    break  # unknown command — stop parsing

            # Follow chain if next_block points forward
            if next_block > block_pos:
                block_pos = next_block
            else:
                break

        if not found_coords:
            return None

        w = x2 - x1 + 1
        h = y2 - y1 + 1
        if w <= 0 or h <= 0 or w > 1920 or h > 1080:
            return None

        # ─────────────────────────────────────────────────────────────────
        pixels = self._decode_vobsub_rle(spu, top_field_offset, bot_field_offset, w, h)
        if not pixels:
            return None

        # ─────────────────────────────────────────────────────────────────
        # Build RGBA pixel array using idx palette ───────────────────────
        # idx_palette is a list of 16 (R,G,B) tuples from the .idx file.
        # pal_idx maps SPU nibble (0-3) → idx_palette index (0-15).
        # alpha_map maps SPU nibble (0-3) → 0-15 opacity.

        # Fallback 4-color palette if no .idx palette available
        FALLBACK = [
            (0, 0, 0),  # 0
            (0, 0, 0),  # 1  (black outline)
            (255, 255, 255),  # 2  (white text)
            (128, 128, 128),  # 3  (gray)
        ]

        def get_rgba(nibble: int) -> tuple[int, int, int, int]:
            n = nibble & 3
            a = (alpha_map[n] * 255) // 15
            if idx_palette and 0 <= pal_idx[n] < len(idx_palette):
                r, g, b = idx_palette[pal_idx[n]]
            else:
                r, g, b = FALLBACK[n]
            return (r, g, b, a)

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pix = img.load()
        for i in range(min(len(pixels), w * h)):
            pix[i % w, i // w] = get_rgba(pixels[i])

        return img

    def _decode_vobsub_rle(
        self,
        data: bytes,
        top_off: int,
        bot_off: int,
        width: int,
        height: int,
    ) -> list[int]:
        """
        Correct DVD VobSub nibble-RLE decoder.

        Two interlaced fields:
          - Top field (even rows 0, 2, 4, …): pixel data at top_off
          - Bottom field (odd rows 1, 3, 5, …): pixel data at bot_off

        Nibble-RLE algorithm (each nibble = 4 bits, big-endian):
          Read nibbles until code word bits [msb..2] are non-zero:
            1 nibble  (n1 >= 4):        count = n1>>2,       color = n1&3
            2 nibbles ((n1n2) >= 0x10): count = (n1n2)>>2,   color = (n1n2)&3
            3 nibbles ((n1n2n3)>=0x40): count = (...)>>2,    color = (...)&3
            4 nibbles (else):           count = (n1n2n3n4)>>2,  color=&3
          count == 0 → fill rest of line
          Nibble stream is byte-aligned at the start of each new row.
        """
        pixels: list[int] = [0] * (width * height)

        def decode_field(field_off: int, rows: list[int]) -> None:
            state = [field_off, True]  # [byte_pos, on_high_nibble]

            def nib() -> int:
                if state[0] >= len(data):
                    return 0
                b = data[state[0]]
                if state[1]:
                    state[1] = False
                    return (b >> 4) & 0xF
                else:
                    state[1] = True
                    state[0] += 1
                    return b & 0xF

            def byte_align() -> None:
                if not state[1]:  # on low nibble → skip to next byte
                    state[1] = True
                    state[0] += 1

            for row in rows:
                x = 0
                while x < width:
                    n1 = nib()
                    if n1 >= 4:
                        count = n1 >> 2
                        color = n1 & 3
                    else:
                        n2 = nib()
                        v2 = (n1 << 4) | n2
                        if v2 >= 0x10:
                            count = v2 >> 2
                            color = v2 & 3
                        else:
                            n3 = nib()
                            v3 = (v2 << 4) | n3
                            if v3 >= 0x40:
                                count = v3 >> 2
                                color = v3 & 3
                            else:
                                n4 = nib()
                                v4 = (v3 << 4) | n4
                                count = v4 >> 2
                                color = v4 & 3

                    if count == 0:
                        count = width - x  # EOL: fill rest of line

                    end_x = min(x + count, width)
                    if row < height:
                        base = row * width
                        for px in range(x, end_x):
                            pixels[base + px] = color
                    x = end_x

                byte_align()  # reset nibble alignment for next row

        even_rows = list(range(0, height, 2))
        odd_rows = list(range(1, height, 2))
        decode_field(top_off, even_rows)
        decode_field(bot_off, odd_rows)

        return pixels


# ============================================================
# MicroDVD Parser  {frame}{frame}text
# ============================================================


class MicroDVDParser:
    """
    Parses MicroDVD .sub files (text-based, frame numbers).
    Yields (start_ms, end_ms, text) — no images needed.
    FPS defaults to 23.976 but can be overridden.
    """

    DEFAULT_FPS = 23.976

    def __init__(self, sub_path: str, fps: float | None = None) -> None:
        self.sub_path = sub_path
        self.fps = fps  # None = auto-detect from first line

    def parse(self) -> Generator[tuple[int, int, str], None, None]:
        """Yield (start_ms, end_ms, text) for each subtitle line."""
        lines = self._read_lines()
        fps = self.fps

        for raw_start, raw_end, text in lines:
            if fps is None:
                # First line {1}{1}23.976 → fps spec
                if raw_start == 1 and raw_end == 1:
                    try:
                        fps = float(text.strip())
                    except ValueError:
                        fps = self.DEFAULT_FPS
                    continue
                fps = self.DEFAULT_FPS

            start_ms = int(raw_start / fps * 1000)
            end_ms = int(raw_end / fps * 1000)
            clean = re.sub(r"\{[^}]*\}", "", text).replace("|", "\n").strip()
            if clean:
                yield (start_ms, end_ms, clean)

    def _read_lines(self) -> list[tuple[int, int, str]]:
        results = []
        pattern = re.compile(r"^\{(\d+)\}\{(\d+)\}(.*)$")
        try:
            with open(self.sub_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = pattern.match(line.strip())
                    if m:
                        results.append((int(m.group(1)), int(m.group(2)), m.group(3)))
        except Exception:
            pass
        return results


# ============================================================
# SubViewer Parser  HH:MM:SS.CC,HH:MM:SS.CC
# ============================================================


class SubViewerParser:
    """
    Parses SubViewer .sub files (text-based, timestamp pairs).
    Yields (start_ms, end_ms, text).
    """

    # Matches: 00:01:23.45,00:01:25.10
    TS_PATTERN = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{2}),(\d{2}):(\d{2}):(\d{2})\.(\d{2})\s*$")

    def parse_file(self, sub_path: str) -> Generator[tuple[int, int, str], None, None]:
        """Yield (start_ms, end_ms, text) for each subtitle block."""
        try:
            with open(sub_path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception:
            return

        blocks = content.split("\n\n")
        for block in blocks:
            lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
            if not lines:
                continue

            # Find timestamp line
            ts_line = None
            text_lines = []
            for line in lines:
                if self.TS_PATTERN.match(line):
                    ts_line = line
                elif ts_line is not None:
                    if not line.startswith("["):  # skip [INFORMATION] blocks
                        text_lines.append(line)

            if ts_line and text_lines:
                m = self.TS_PATTERN.match(ts_line)
                if m:
                    start_ms = self._to_ms(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
                    end_ms = self._to_ms(int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8)))
                    text = "\n".join(text_lines).replace("[br]", "\n")
                    yield (start_ms, end_ms, text)

    @staticmethod
    def _to_ms(h: int, m: int, s: int, cs: int) -> int:
        """Convert HH:MM:SS.CC (centiseconds) → milliseconds."""
        return (h * 3_600 + m * 60 + s) * 1_000 + cs * 10


# ============================================================
# Unified .sub entry point used by MediaEngine
# ============================================================


def parse_sub_file(
    sub_path: str,
    fps: float | None = None,
) -> tuple[str, Generator]:
    """
    Auto-detect .sub type and return (sub_type, generator).

    For 'vobsub'   → generator yields (start_ms, end_ms, PIL.Image)
    For 'microdvd' → generator yields (start_ms, end_ms, text:str)
    For 'subviewer'→ generator yields (start_ms, end_ms, text:str)
    For 'unknown'  → raises ValueError
    """
    sub_type = detect_sub_type(sub_path)

    if sub_type == "vobsub":
        return sub_type, VobSubParser(sub_path).parse()

    elif sub_type == "microdvd":
        return sub_type, MicroDVDParser(sub_path, fps=fps).parse()

    elif sub_type == "subviewer":
        return sub_type, SubViewerParser().parse_file(sub_path)

    else:
        raise ValueError(
            f"لم أتعرف على نوع ملف .sub: {sub_path}\nالأنواع المدعومة: VobSub (+ .idx), MicroDVD, SubViewer"
        )
