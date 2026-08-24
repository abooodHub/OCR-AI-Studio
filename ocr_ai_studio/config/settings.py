from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ocr_ai_studio.domain.models import EngineKind


def default_data_dir() -> Path:
    root = os.getenv("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "OCR-AI Studio"


@dataclass(slots=True)
class AppSettings:
    engine: str = EngineKind.LM_STUDIO.value
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "qwen/qwen2.5-vl-7b"
    output_dir: str = ""
    export_format: str = "srt"
    theme: str = "dark"
    max_retries: int = 3
    request_timeout_seconds: int = 90
    api_key: str = ""
    auto_start_engine: bool = True
    stop_owned_engine_on_exit: bool = False
    engine_executables: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        string_fields = {
            "engine": self.engine,
            "base_url": self.base_url,
            "model": self.model,
            "output_dir": self.output_dir,
            "export_format": self.export_format,
            "theme": self.theme,
            "api_key": self.api_key,
        }
        for name, value in string_fields.items():
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        if type(self.max_retries) is not int:
            raise ValueError("max_retries must be an integer")
        if type(self.request_timeout_seconds) is not int:
            raise ValueError("request_timeout_seconds must be an integer")
        if type(self.auto_start_engine) is not bool:
            raise ValueError("auto_start_engine must be a boolean")
        if type(self.stop_owned_engine_on_exit) is not bool:
            raise ValueError("stop_owned_engine_on_exit must be a boolean")
        if not isinstance(self.engine_executables, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.engine_executables.items()
        ):
            raise ValueError("engine_executables must map engine names to paths")
        if self.engine not in {item.value for item in EngineKind}:
            raise ValueError(f"Unsupported engine: {self.engine}")
        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ValueError("Base URL must start with http:// or https://")
        if not self.model.strip():
            raise ValueError("Model name is required")
        if self.export_format not in {"srt", "vtt", "ass", "txt"}:
            raise ValueError("Unsupported export format")
        if not 1 <= self.max_retries <= 10:
            raise ValueError("max_retries must be between 1 and 10")
        if not 10 <= self.request_timeout_seconds <= 600:
            raise ValueError("request_timeout_seconds must be between 10 and 600")


class SettingsStore:
    """JSON settings store using atomic replacement to prevent corruption."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_dir() / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Settings root must be a JSON object")
            allowed = AppSettings.__dataclass_fields__.keys()
            settings = AppSettings(**{key: value for key, value in raw.items() if key in allowed})
            settings.validate()
            return settings
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                payload = asdict(settings)
                payload.pop("api_key", None)
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            temp_path = Path(temp_name)
            if temp_path.exists():
                temp_path.unlink()
