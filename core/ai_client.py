"""
core/ai_client.py — Vision AI Client (LM Studio / Ollama)
"""

import base64
import logging
import time

try:
    from openai import APIError, APITimeoutError, OpenAI, RateLimitError
except ImportError as exc:
    raise ImportError(
        "فشل استيراد عميل OpenAI. تأكد من تثبيت الحزم المتوافقة: "
        "pip install -U openai httpx"
    ) from exc

logger = logging.getLogger("core.ai_client")

_SYSTEM_PROMPT = (
    "You are a subtitle OCR engine. "
    "Look at the image and extract ONLY the subtitle text you see. "
    "Output the raw text exactly as it appears — no commentary, "
    "no translation, no markdown, no quotes. "
    "If there is absolutely no readable text, output exactly: EMPTY"
)

_EMPTY_TOKENS = {"EMPTY", "EMPTY.", "[EMPTY]", "(EMPTY)", ""}

VISION_KEYWORDS = (
    "vl", "vision", "llava", "minicpm", "pixtral", "molmo",
    "cogvlm", "omni", "qwen2-vl", "qwen2.5-vl", "paligemma",
    "gemma-2-vision", "internlm-xcomposer", "deepseek-vl"
)


def is_vision_model(model_name: str) -> bool:
    """Return True if the model name suggests Vision Input capability."""
    name_lower = model_name.lower()
    return any(kw in name_lower for kw in VISION_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════
class AIClient:
    """Resilient client wrapper for local Vision OpenAI endpoints (LM Studio / Ollama)."""

    MAX_RETRIES = 3
    _BACKOFF = [1, 2, 4]   # seconds between retries

    def __init__(self, config: dict) -> None:
        self.engine: str = config.get("engine", "lmstudio")
        self.base_url: str = config.get("base_url", "http://localhost:1234/v1")
        self.api_key: str = config.get("api_key", "lm-studio")
        self.model: str = config.get("model", "qwen/qwen2.5-vl-7b")
        self.client: OpenAI = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=60.0,
            max_retries=0,
        )

    def ocr(self, image_bytes: bytes) -> str:
        """Send JPEG bytes to the vision model and return extracted text."""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        messages = self._build_messages(b64)

        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=300,
                    temperature=0,
                )
                text = self._extract_message_text(response)
                if text.upper().strip(".[] ") in _EMPTY_TOKENS or text == "":
                    return ""
                return text

            except (RateLimitError, APITimeoutError) as exc:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self._BACKOFF[attempt]
                    logger.warning(
                        "Retry %d/%d in %ds: %s", attempt + 1, self.MAX_RETRIES, delay, exc
                    )
                    time.sleep(delay)
                else:
                    logger.error("All %d attempts failed: %s", self.MAX_RETRIES, exc)
                    return ""

            except APIError as exc:
                logger.error("API error: %s", exc)
                return ""

            except Exception as exc:  # noqa: BLE001
                logger.error("Unexpected error (attempt %d): %s", attempt + 1, exc)
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self._BACKOFF[attempt])
                else:
                    return ""

        return ""

    def list_models(self) -> list[str]:
        """Return a list of available model names from the server."""
        try:
            response = self.client.models.list()
            models = []
            for item in getattr(response, "data", []) or []:
                model_id = getattr(item, "id", None) or getattr(item, "name", None) or str(item)
                if model_id:
                    models.append(model_id)
            return models
        except Exception as exc:  # noqa: BLE001
            logger.debug("Model listing failed: %s", exc)
            return []

    def get_real_model_name(self) -> str:
        """Fetch the real loaded model name from the server, matching user selection intelligently."""
        models = self.list_models()
        if not models:
            return self.model

        if self.model in models:
            return self.model

        user_lower = self.model.lower().strip()
        if user_lower and user_lower != "local-model":
            for m in models:
                if user_lower in m.lower() or m.lower() in user_lower:
                    return m
            return self.model

        vision_models = [m for m in models if is_vision_model(m)]
        if vision_models:
            return vision_models[0]

        return models[0]

    def check_model(self) -> tuple[bool, str, list[str], bool]:
        """Check whether the configured model endpoint can accept a request."""
        models = self.list_models()
        target_model = self.get_real_model_name()
        is_vision = is_vision_model(target_model)

        vision_models = [m for m in models if is_vision_model(m)]
        non_vision_models = [m for m in models if not is_vision_model(m)]
        sorted_models = [f"👁️ {m} (Vision Input)" for m in vision_models] + non_vision_models

        try:
            response = self.client.chat.completions.create(
                model=target_model,
                messages=[
                    {
                        "role": "user",
                        "content": "Please reply with OK.",
                    }
                ],
                max_tokens=5,
                temperature=0,
            )
            real_name = getattr(response, "model", None) or target_model
            if real_name:
                self.model = real_name

            return True, self.model, sorted_models, is_vision

        except Exception as exc:  # noqa: BLE001
            logger.error("Model health check failed: %s", exc)
            err_msg = str(exc)
            if "No model loaded" in err_msg or "404" in err_msg:
                return False, "لا يوجد موديل محمل في الخادم (LM Studio / Ollama)", sorted_models, False
            if "Connection refused" in err_msg or "Failed to connect" in err_msg or "Cannot connect" in err_msg:
                return False, "تعذر الاتصال بالخادم (تأكد من تشغيل LM Studio أو Ollama)", sorted_models, False
            return False, err_msg, sorted_models, False

    @staticmethod
    def _extract_message_text(response) -> str:
        try:
            choice = response.choices[0]
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None)
            if content is None:
                content = getattr(choice, "text", "")

            if isinstance(content, str):
                return content.strip()

            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(str(item))
                return "\n".join(part.strip() for part in parts if part)

            if isinstance(content, dict):
                return str(content.get("text", "")).strip()

            return str(content).strip()
        except Exception:
            return ""

    @staticmethod
    def _build_messages(b64_image: str) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract the subtitle text from this image.",
                    },
                ],
            },
        ]
