from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr_ai_studio.config.settings import AppSettings, SettingsStore
from ocr_ai_studio.domain.models import EngineKind


class SettingsTests(TestCase):
    def test_settings_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            settings = AppSettings(model="vision-model", export_format="vtt")
            store.save(settings)
            self.assertEqual(store.load().model, "vision-model")
            self.assertEqual(store.load().export_format, "vtt")

    def test_invalid_json_falls_back_to_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(SettingsStore(path).load(), AppSettings())

    def test_wrong_json_types_fall_back_to_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"base_url": 7}', encoding="utf-8")
            self.assertEqual(SettingsStore(path).load(), AppSettings())

    def test_unsloth_settings_are_valid_and_secret_is_not_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            settings = AppSettings(
                engine=EngineKind.UNSLOTH.value,
                base_url="http://127.0.0.1:8888/v1",
                model="unsloth/vision-model",
                api_key="top-secret",
            )
            store.save(settings)
            self.assertNotIn("top-secret", path.read_text(encoding="utf-8"))
            loaded = store.load()
            self.assertEqual(loaded.engine, EngineKind.UNSLOTH.value)
            self.assertEqual(loaded.api_key, "")
