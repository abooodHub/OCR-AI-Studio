from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr_ai_studio.domain.models import EngineKind, JobRequest, StreamInfo, SubtitleCue
from ocr_ai_studio.persistence.database import ProjectDatabase


class PersistenceTests(TestCase):
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
