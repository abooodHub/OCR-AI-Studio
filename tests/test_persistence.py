import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr_ai_studio.domain.models import (
    EngineKind,
    JobRequest,
    QueueStatus,
    StreamInfo,
    SubtitleCue,
)
from ocr_ai_studio.persistence.database import ProjectDatabase


class PersistenceTests(TestCase):
    def test_queue_survives_restart_and_running_job_is_requeued(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"sample")
            path = root / "projects.sqlite3"
            request = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(1, 4, "hdmv_pgs_subtitle", "ara", "Arabic"),
                EngineKind.OLLAMA,
                "http://127.0.0.1:11434/v1",
                "vision-model",
            )
            database = ProjectDatabase(path)
            queue_id = database.enqueue(request)
            database.set_queue_status(queue_id, QueueStatus.RUNNING)

            reopened = ProjectDatabase(path)
            jobs = reopened.queue_jobs()

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].status, QueueStatus.QUEUED)
            self.assertEqual(jobs[0].request.stream.source_index, 4)
            self.assertEqual(jobs[0].request.stream.language, "ara")
            self.assertEqual(jobs[0].request.base_url, "http://127.0.0.1:11434/v1")

    def test_existing_database_is_migrated_for_timing_and_review_images(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "projects.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE projects (id TEXT PRIMARY KEY);
                    CREATE TABLE frames (
                        project_id TEXT NOT NULL,
                        frame_index INTEGER NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT,
                        PRIMARY KEY(project_id, frame_index)
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            ProjectDatabase(database_path)

            connection = sqlite3.connect(database_path)
            try:
                project_columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
                frame_columns = {row[1] for row in connection.execute("PRAGMA table_info(frames)")}
            finally:
                connection.close()
            self.assertIn("timing_status", project_columns)
            self.assertIn("timing_origin_status", project_columns)
            self.assertIn("timing_offset_ms", project_columns)
            self.assertIn("image_jpeg", frame_columns)
            self.assertIn("source_start_ms", frame_columns)
            self.assertIn("source_end_ms", frame_columns)

    def test_project_identity_changes_with_stream_and_model(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"sample")
            stream = StreamInfo(ordinal=0, source_index=2, codec="hdmv_pgs_subtitle")
            base = JobRequest(
                source,
                root / "out.srt",
                stream,
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-a",
            )
            other_stream = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(1, 3, "hdmv_pgs_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-a",
            )
            other_model = JobRequest(
                source,
                root / "out.srt",
                stream,
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-b",
            )
            self.assertNotEqual(ProjectDatabase.project_id(base), ProjectDatabase.project_id(other_stream))
            self.assertNotEqual(ProjectDatabase.project_id(base), ProjectDatabase.project_id(other_model))

    def test_vobsub_project_identity_includes_the_companion_idx_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "subtitle.sub"
            companion = root / "subtitle.idx"
            source.write_bytes(b"\x00\x00\x01\xba")
            companion.write_text("first", encoding="utf-8")
            request = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(0, 0, "dvd_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            first_id = ProjectDatabase.project_id(request)
            companion.write_text("changed companion", encoding="utf-8")
            second_id = ProjectDatabase.project_id(request)

            self.assertNotEqual(first_id, second_id)

    def test_cue_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"sample")
            database = ProjectDatabase(root / "projects.sqlite3")
            request = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(0, 2, "hdmv_pgs_subtitle"),
                EngineKind.OLLAMA,
                "http://127.0.0.1:11434/v1",
                "qwen-vl",
            )
            project_id = database.create_or_resume(request)
            database.save_cue(project_id, SubtitleCue(100, 900, "مرحبا", frame_index=4))
            self.assertEqual(database.load_cues(project_id)[0].text, "مرحبا")

    def test_review_frames_and_quality_report_include_images_and_failures(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"sample")
            database = ProjectDatabase(root / "projects.sqlite3")
            request = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(0, 2, "hdmv_pgs_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            project_id = database.create_or_resume(request)
            database.set_timing_status(project_id, "validated")
            database.set_totals(project_id, 2)
            database.record_frame(
                project_id,
                0,
                1_000,
                2_000,
                "done",
                image_jpeg=b"jpeg-one",
            )
            database.save_cue(project_id, SubtitleCue(1_000, 2_000, "قديم", frame_index=0))
            database.record_frame(
                project_id,
                1,
                2_100,
                3_000,
                "failed",
                error="timeout",
                image_jpeg=b"jpeg-two",
            )

            frames = database.review_frames(project_id)
            report = database.quality_report(project_id)

            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[1].status, "failed")
            self.assertEqual(frames[1].image_jpeg, b"jpeg-two")
            self.assertEqual(report.total_frames, 2)
            self.assertEqual(report.recognized_frames, 1)
            self.assertEqual(report.failed_frames, 1)
            self.assertEqual(report.timing_status, "validated")
            self.assertLess(report.score, 100)

            database.update_cue_text(project_id, 0, "نص مصحح")
            self.assertEqual(database.load_cues(project_id)[0].text, "نص مصحح")

    def test_timing_can_be_edited_and_shifted_without_becoming_negative(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"sample")
            database = ProjectDatabase(root / "projects.sqlite3")
            request = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(0, 2, "hdmv_pgs_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            project_id = database.create_or_resume(request)
            database.record_frame(project_id, 0, 1_000, 2_000, "done")
            database.save_cue(project_id, SubtitleCue(1_000, 2_000, "مرحبا", frame_index=0))
            database.set_totals(project_id, 1)

            database.update_cue_timing(project_id, 0, 1_100, 2_250)
            self.assertEqual(database.load_cues(project_id)[0].start_ms, 1_100)
            self.assertEqual(database.shift_project_timing(project_id, 500), 500)
            shifted = database.load_cues(project_id)[0]
            self.assertEqual((shifted.start_ms, shifted.end_ms), (1_600, 2_750))
            self.assertEqual(database.quality_report(project_id).timing_status, "manually_adjusted")

            with self.assertRaises(ValueError):
                database.shift_project_timing(project_id, -2_000)
            self.assertEqual(database.project_timing_offset(project_id), 500)

            database.restore_source_timing(project_id)
            restored = database.load_cues(project_id)[0]
            self.assertEqual((restored.start_ms, restored.end_ms), (1_000, 2_000))
            self.assertEqual(database.project_timing_offset(project_id), 0)

    def test_project_timing_shift_is_blocked_until_all_frames_are_indexed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mks"
            source.write_bytes(b"sample")
            database = ProjectDatabase(root / "projects.sqlite3")
            request = JobRequest(
                source,
                root / "out.srt",
                StreamInfo(0, 2, "hdmv_pgs_subtitle"),
                EngineKind.LM_STUDIO,
                "http://127.0.0.1:1234/v1",
                "vision-model",
            )
            project_id = database.create_or_resume(request)
            database.set_totals(project_id, 2)
            database.record_frame(project_id, 0, 1_000, 2_000, "done")

            with self.assertRaises(ValueError):
                database.shift_project_timing(project_id, 100)
