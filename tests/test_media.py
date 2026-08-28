import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ocr_ai_studio.domain.models import StreamInfo
from ocr_ai_studio.media.bitmap_parser import PGSParser
from ocr_ai_studio.media.ffmpeg import FFmpegError, FFmpegService


class MediaTests(TestCase):
    def test_dvb_and_xsub_are_classified_as_bitmap_subtitles(self) -> None:
        self.assertTrue(StreamInfo(0, 0, "dvb_subtitle").is_bitmap)
        self.assertTrue(StreamInfo(0, 0, "xsub").is_bitmap)

    def test_pgs_event_count_ignores_compositions_without_renderable_objects(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "subtitle.sup"
            pcs_data = bytes(10) + b"\x01"
            pcs = b"PG" + struct.pack(">IIBH", 90_000, 90_000, PGSParser.SEG_PCS, len(pcs_data))
            end = b"PG" + struct.pack(">IIBH", 90_000, 90_000, PGSParser.SEG_END, 0)
            source.write_bytes(pcs + pcs_data + end)

            self.assertEqual(PGSParser(str(source)).count_events(), 0)

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

    def test_text_sub_is_not_misclassified_as_bitmap(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "subtitle.sub"
            source.write_text("{1}{25}Hello", encoding="utf-8")
            stream = FFmpegService(ffprobe="definitely-missing").probe_subtitles(source)[0]
            self.assertEqual(stream.codec, "microdvd")
            self.assertFalse(stream.is_bitmap)

    def test_vobsub_idx_timestamps_are_counted_in_milliseconds(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "subtitle.idx"
            source.write_text(
                "timestamp: 00:00:21:855, filepos: 000000000\n"
                "timestamp: 01:35:11:487, filepos: 000001000\n",
                encoding="utf-8",
            )
            service = FFmpegService()
            self.assertEqual(service._idx_timestamp_bounds(source), (21_855, 5_711_487))
            self.assertEqual(service.count_vobsub_frames(source), 2)

    def test_pgs_extraction_preserves_container_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.mks"
            destination = root / "subtitle.sup"
            source.write_bytes(b"matroska")

            def successful_extract(command: list[str], timeout: int) -> None:
                self.assertEqual(timeout, 600)
                destination.write_bytes(b"PG")

            service = FFmpegService()
            with (
                patch.object(
                    service,
                    "_probe_subtitle_timestamp_bounds",
                    return_value=(21_855, 5_711_487),
                ),
                patch.object(
                    service,
                    "_sup_timestamp_bounds",
                    return_value=(21_855, 5_711_487),
                ),
                patch.object(service, "_run", side_effect=successful_extract) as run,
            ):
                service.extract_pgs(source, 0, destination)

            command = run.call_args.args[0]
            self.assertIn("-copyts", command)
            self.assertLess(command.index("-copyts"), command.index("-i"))

    def test_pgs_extraction_stops_before_ocr_when_timestamps_shift(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.mks"
            destination = root / "subtitle.sup"
            source.write_bytes(b"matroska")

            def shifted_extract(_command: list[str], timeout: int) -> None:
                self.assertEqual(timeout, 600)
                destination.write_bytes(b"PG")

            service = FFmpegService()
            with (
                patch.object(
                    service,
                    "_probe_subtitle_timestamp_bounds",
                    return_value=(21_855, 5_711_487),
                ),
                patch.object(
                    service,
                    "_sup_timestamp_bounds",
                    return_value=(0, 5_689_632),
                ),
                patch.object(service, "_run", side_effect=shifted_extract),
            ):
                with self.assertRaisesRegex(FFmpegError, "توقيت PGS"):
                    service.extract_pgs(source, 0, destination)

            self.assertFalse(destination.exists())

    def test_embedded_vobsub_extraction_preserves_container_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.mks"
            destination = root / "subtitle.idx"
            source.write_bytes(b"matroska")

            commands: list[list[str]] = []

            def successful_extract(command: list[str], timeout: int) -> SimpleNamespace:
                commands.append(command)
                if "-J" in command:
                    self.assertEqual(timeout, 120)
                    return SimpleNamespace(
                        stdout='{"tracks":[{"id":0,"type":"video"},'
                        '{"id":4,"type":"subtitles"}]}'
                    )
                self.assertEqual(timeout, 600)
                destination.write_text(
                    "timestamp: 00:00:21:855, filepos: 000000000\n"
                    "timestamp: 01:35:11:487, filepos: 000001000\n",
                    encoding="utf-8",
                )
                destination.with_suffix(".sub").write_bytes(b"\x00\x00\x01\xba")
                return SimpleNamespace(stdout="")

            service = FFmpegService()
            with (
                patch.object(
                    service,
                    "_probe_subtitle_timestamp_bounds",
                    return_value=(21_855, 5_711_487),
                ),
                patch.object(service, "_find_executable", side_effect=lambda name: f"{name}.exe"),
                patch.object(service, "_run", side_effect=successful_extract),
            ):
                service.extract_vobsub(source, 0, destination)

            self.assertEqual(commands[0][1], "-J")
            self.assertEqual(commands[1][1], "tracks")
            self.assertEqual(commands[1][-1], f"4:{destination}")
            self.assertTrue(destination.with_suffix(".sub").exists())

    def test_embedded_vobsub_extraction_removes_shifted_pair(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.mks"
            destination = root / "subtitle.idx"
            source.write_bytes(b"matroska")

            def shifted_extract(command: list[str], timeout: int) -> SimpleNamespace:
                if "-J" in command:
                    self.assertEqual(timeout, 120)
                    return SimpleNamespace(stdout='{"tracks":[{"id":2,"type":"subtitles"}]}')
                self.assertEqual(timeout, 600)
                destination.write_text(
                    "timestamp: 00:00:00:000, filepos: 000000000\n"
                    "timestamp: 00:00:01:000, filepos: 000001000\n",
                    encoding="utf-8",
                )
                destination.with_suffix(".sub").write_bytes(b"\x00\x00\x01\xba")
                return SimpleNamespace(stdout="")

            service = FFmpegService()
            with (
                patch.object(
                    service,
                    "_probe_subtitle_timestamp_bounds",
                    return_value=(21_855, 30_000),
                ),
                patch.object(service, "_find_executable", side_effect=lambda name: f"{name}.exe"),
                patch.object(service, "_run", side_effect=shifted_extract),
            ):
                with self.assertRaisesRegex(FFmpegError, "توقيت VobSub"):
                    service.extract_vobsub(source, 0, destination)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".sub").exists())

    def test_constant_vobsub_timestamp_shift_is_corrected_and_verified(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.mks"
            destination = root / "subtitle.idx"
            source.write_bytes(b"matroska")

            def normalized_extract(command: list[str], timeout: int) -> SimpleNamespace:
                self.assertIn(timeout, {120, 600})
                if "-J" in command:
                    return SimpleNamespace(stdout='{"tracks":[{"id":2,"type":"subtitles"}]}')
                destination.write_text(
                    "timestamp: 00:00:00:000, filepos: 000000000\n"
                    "timestamp: 00:00:10:000, filepos: 000001000\n",
                    encoding="utf-8",
                )
                destination.with_suffix(".sub").write_bytes(b"\x00\x00\x01\xba")
                return SimpleNamespace(stdout="")

            service = FFmpegService()
            with (
                patch.object(
                    service,
                    "_probe_subtitle_timestamp_bounds",
                    return_value=(21_855, 31_855),
                ),
                patch.object(service, "_find_executable", side_effect=lambda name: f"{name}.exe"),
                patch.object(service, "_run", side_effect=normalized_extract),
            ):
                service.extract_vobsub(source, 0, destination)

            self.assertEqual(service._idx_timestamp_bounds(destination), (21_855, 31_855))

    def test_embedded_vobsub_requires_mkvtoolnix(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.mks"
            source.write_bytes(b"matroska")
            service = FFmpegService(mkvextract="missing-extract", mkvmerge="missing-merge")
            with (
                patch.object(service, "_probe_subtitle_timestamp_bounds", return_value=(0, 1_000)),
                patch.object(service, "_find_executable", return_value=None),
            ):
                with self.assertRaisesRegex(FFmpegError, "MKVToolNix"):
                    service.extract_vobsub(source, 0, root / "subtitle.idx")

    def test_vob_container_is_not_sent_through_matroska_extractor(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "VTS_01_1.VOB"
            source.write_bytes(b"dvd")
            with self.assertRaisesRegex(FFmpegError, "MKV/MKS"):
                FFmpegService().extract_vobsub(source, 0, root / "subtitle.idx")
