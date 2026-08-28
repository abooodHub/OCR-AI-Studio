from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ocr_ai_studio.domain.models import SubtitleCue


@dataclass(frozen=True, slots=True)
class TimingValidationReport:
    valid: bool
    cue_count: int
    first_start_ms: int | None
    last_end_ms: int | None
    overlaps: int
    suspicious_short: int
    suspicious_long: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def summary(self) -> str:
        if not self.valid:
            return "فشل فحص التوقيت: " + "، ".join(self.errors)
        if self.warnings:
            return "التوقيت صالح مع ملاحظات: " + "، ".join(self.warnings)
        return "التوقيت سليم"


class SubtitleTimingValidator:
    """Validate structural timing without changing source timestamps."""

    TRUSTED_TIMING_STATES = {"validated", "source_native", "manually_adjusted"}

    def validate(
        self,
        cues: Iterable[SubtitleCue],
        *,
        timing_status: str,
    ) -> TimingValidationReport:
        ordered = sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms, cue.frame_index or -1))
        errors: list[str] = []
        warnings: list[str] = []

        if not ordered:
            errors.append("لا توجد ترجمات نصية")
        if timing_status not in self.TRUSTED_TIMING_STATES:
            errors.append("مصدر التوقيت غير موثّق")

        invalid_ranges = sum(
            cue.start_ms < 0 or cue.end_ms <= cue.start_ms for cue in ordered
        )
        if invalid_ranges:
            errors.append(f"{invalid_ranges} توقيت غير صالح")

        overlaps = sum(
            current.start_ms < previous.end_ms
            for previous, current in zip(ordered, ordered[1:], strict=False)
        )
        suspicious_short = sum(cue.end_ms - cue.start_ms < 100 for cue in ordered)
        suspicious_long = sum(cue.end_ms - cue.start_ms > 20_000 for cue in ordered)

        if overlaps:
            warnings.append(f"{overlaps} تداخل من المصدر")
        if suspicious_short:
            warnings.append(f"{suspicious_short} مدة قصيرة جدًا")
        if suspicious_long:
            warnings.append(f"{suspicious_long} مدة طويلة جدًا")

        return TimingValidationReport(
            valid=not errors,
            cue_count=len(ordered),
            first_start_ms=ordered[0].start_ms if ordered else None,
            last_end_ms=ordered[-1].end_ms if ordered else None,
            overlaps=overlaps,
            suspicious_short=suspicious_short,
            suspicious_long=suspicious_long,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
