from unittest import TestCase

from ocr_ai_studio.domain.models import SubtitleCue
from ocr_ai_studio.processing.timing_validator import SubtitleTimingValidator


class TimingValidatorTests(TestCase):
    def test_valid_source_timing_passes_without_modification(self) -> None:
        cues = [
            SubtitleCue(1_000, 2_000, "الأول", frame_index=0),
            SubtitleCue(2_500, 3_500, "الثاني", frame_index=1),
        ]
        report = SubtitleTimingValidator().validate(cues, timing_status="validated")
        self.assertTrue(report.valid)
        self.assertEqual(report.first_start_ms, 1_000)
        self.assertEqual(report.last_end_ms, 3_500)
        self.assertEqual(report.summary, "التوقيت سليم")

    def test_untrusted_timing_state_blocks_final_export(self) -> None:
        cues = [SubtitleCue(1_000, 2_000, "نص", frame_index=0)]
        report = SubtitleTimingValidator().validate(cues, timing_status="pending")
        self.assertFalse(report.valid)
        self.assertIn("مصدر التوقيت غير موثّق", report.errors)

    def test_source_overlap_is_reported_without_rewriting_timestamps(self) -> None:
        cues = [
            SubtitleCue(1_000, 3_000, "الأول", frame_index=0),
            SubtitleCue(2_500, 4_000, "الثاني", frame_index=1),
        ]
        report = SubtitleTimingValidator().validate(cues, timing_status="source_native")
        self.assertTrue(report.valid)
        self.assertEqual(report.overlaps, 1)
        self.assertIn("تداخل", report.summary)
