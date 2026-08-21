from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError
from PIL import Image, ImageDraw, ImageFont

from ocr_ai_studio.domain.models import EngineKind

SYSTEM_PROMPT = (
    "You are a precise subtitle OCR engine. Read only the subtitle visible in the image. "
    "Return the exact text, preserving line breaks. Do not translate, explain, quote, or use markdown. "
    "If no subtitle is readable, return exactly EMPTY."
)
EMPTY_RESPONSES = {"EMPTY", "[EMPTY]", "(EMPTY)"}


class OCRRequestError(RuntimeError):
    """An OCR request failed and must never be interpreted as an empty subtitle."""


@dataclass(frozen=True, slots=True)
class ModelCheck:
    ready: bool
    supports_vision: bool
    model: str
    latency_ms: int
    message: str


class VisionClient:
    def __init__(
        self,
        engine: EngineKind,
        base_url: str,
        model: str,
        timeout_seconds: int = 90,
        max_retries: int = 3,
        api_key: str = "",
    ) -> None:
        self.engine = engine
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.max_retries = max_retries
        resolved_api_key = self._resolve_api_key(engine, api_key)
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=resolved_api_key,
            timeout=float(timeout_seconds),
            max_retries=0,
        )

    def list_models(self) -> list[str]:
        try:
            result = self.client.models.list()
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            raise OCRRequestError(f"Unable to list models: {exc}") from exc
        return [str(item.id) for item in result.data if getattr(item, "id", None)]

    def check_model(self) -> ModelCheck:
        """Perform a real image request so text-only models cannot pass the check."""
        probe = Image.new("RGB", (640, 200), "white")
        try:
            font = ImageFont.truetype("arial.ttf", 48)
        except OSError:
            font = ImageFont.load_default(size=48)
        ImageDraw.Draw(probe).text((40, 60), "VISION TEST 2847", fill="black", font=font)
        buffer = io.BytesIO()
        probe.save(buffer, "JPEG", quality=90)
        started = time.perf_counter()
        try:
            text = self.ocr(buffer.getvalue(), allow_empty=False)
        except OCRRequestError as exc:
            return ModelCheck(
                ready=False,
                supports_vision=False,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                message=str(exc),
            )
        normalized = "".join(character for character in text if character.isalnum()).upper()
        supports_vision = "2847" in normalized
        return ModelCheck(
            ready=supports_vision,
            supports_vision=supports_vision,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=(
                "Vision request succeeded" if supports_vision else "The model did not read the test image"
            ),
        )

    def ocr(self, image_bytes: bytes, *, allow_empty: bool = True) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"},
                    },
                    {"type": "text", "text": "Extract the subtitle text."},
                ],
            },
        ]

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0,
                    max_tokens=500,
                )
                content = response.choices[0].message.content
                text = self._normalize_content(content)
                if not text:
                    raise OCRRequestError("AI server returned an empty or invalid response")
                if self._is_empty(text):
                    if allow_empty:
                        return ""
                    raise OCRRequestError("The vision probe returned no readable text")
                return text
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(min(2**attempt, 4))
                    continue
            except APIError as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code in {401, 403}:
                    raise OCRRequestError("AI server rejected the API key") from exc
                if isinstance(status_code, int) and status_code >= 500:
                    last_error = exc
                    if attempt + 1 < self.max_retries:
                        time.sleep(min(2**attempt, 4))
                        continue
                raise OCRRequestError(f"AI server rejected the image request: {exc}") from exc
            except (AttributeError, IndexError, TypeError) as exc:
                raise OCRRequestError("AI server returned an invalid response") from exc
        raise OCRRequestError(f"OCR request failed after {self.max_retries} attempts: {last_error}")

    @staticmethod
    def _normalize_content(content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or ""))
                else:
                    text = getattr(part, "text", "")
                    if text:
                        parts.append(str(text))
            return "\n".join(item.strip() for item in parts if item.strip())
        return ""

    @staticmethod
    def _is_empty(text: str) -> bool:
        normalized = text.strip().strip(". ").upper()
        return normalized in EMPTY_RESPONSES

    @staticmethod
    def _resolve_api_key(engine: EngineKind, provided: str) -> str:
        if provided.strip():
            return provided.strip()
        if engine is EngineKind.UNSLOTH:
            return os.getenv("UNSLOTH_API_KEY", "unsloth-local")
        if engine is EngineKind.CUSTOM:
            return os.getenv("OCR_AI_API_KEY", "local")
        if engine is EngineKind.OLLAMA:
            return "ollama"
        return "lm-studio"
