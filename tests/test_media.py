from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr_ai_studio.media.ffmpeg import FFmpegService


class MediaTests(TestCase):
    def test_standalone_sup_is_detected_without_running_ffprobe(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "subtitle.sup"
            source.write_bytes(b"PG")
            stream = FFmpegService(ffprobe="definitely-missing").probe_subtitles(source)[0]
            self.assertTrue(stream.is_bitmap)
            self.assertEqual(stream.ordinal, 0)

    def test_standalone_idx_is_detected_as_vobsub(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "subtitle.idx"
            source.write_text("# VobSub index file", encoding="utf-8")
            stream = FFmpegService(ffprobe="definitely-missing").probe_subtitles(source)[0]
            self.assertEqual(stream.codec, "dvd_subtitle")
