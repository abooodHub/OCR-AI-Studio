from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr_ai_studio.domain.models import SubtitleCue
from ocr_ai_studio.exporters.writers import SubtitleExporter, merge_adjacent_cues
from ocr_ai_studio.media.ffmpeg import FFmpegService


class ExporterTests(TestCase):
    def test_identical_text_across_large_gap_is_not_merged(self) -> None:
        cues = [
            SubtitleCue(0, 1_000, "Same"),
            SubtitleCue(10_000, 11_000, "Same"),
        ]
        self.assertEqual(len(merge_adjacent_cues(cues)), 2)

    def test_adjacent_identical_text_is_merged(self) -> None:
        cues = [
            SubtitleCue(0, 1_000, "Same"),
            SubtitleCue(1_100, 2_000, "Same"),
        ]
        merged = merge_adjacent_cues(cues)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].end_ms, 2_000)

    def test_srt_is_written_atomically_with_expected_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.srt"
            SubtitleExporter().export([SubtitleCue(1_234, 3_456, "Hello")], output, "srt")
            content = output.read_text(encoding="utf-8")
            self.assertIn("00:00:01,234 --> 00:00:03,456", content)

    def test_srt_to_plain_text_removes_indexes_and_timestamps(self) -> None:
        source = "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n"
        self.assertEqual(FFmpegService._srt_to_text(source), "Hello\nWorld\n")
