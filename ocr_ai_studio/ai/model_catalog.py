from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from ocr_ai_studio.domain.models import EngineKind


class ModelCatalogError(RuntimeError):
    """A provider model catalog could not be queried safely."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_id: str
    provider: EngineKind
    loaded: bool | None = None
    supports_vision: bool | None = None
    size_bytes: int | None = None
    quantization: str = ""
    context_length: int | None = None


class ModelCatalogClient:
    def __init__(
        self,
        engine: EngineKind,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 15,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.engine = engine
        self.base_url = base_url.rstrip("/")
        self.origin = self._origin(self.base_url)
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def list_models(self) -> list[ModelInfo]:
        if self.engine is EngineKind.LM_STUDIO:
            models = self._list_lm_studio()
        elif self.engine is EngineKind.OLLAMA:
            models = self._list_ollama()
        else:
            models = self._list_openai_compatible()
        return sorted(models, key=lambda item: (item.loaded is not True, item.model_id.casefold()))

    def _list_lm_studio(self) -> list[ModelInfo]:
        try:
            payload = self._request_json(
                "GET",
                f"{self.origin}/api/v0/models",
                headers=self._auth_headers(),
            )
            models = []
            for item in payload.get("data", []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                model_type = str(item.get("type") or "").casefold()
                architecture = str(item.get("arch") or "").casefold()
                models.append(
                    ModelInfo(
                        model_id=str(item["id"]),
                        provider=self.engine,
                        loaded=str(item.get("state") or "").casefold() == "loaded",
                        supports_vision=model_type == "vlm"
                        or any(token in architecture for token in ("vision", "vl", "llava")),
                        size_bytes=self._optional_int(item.get("size_bytes")),
                        quantization=str(item.get("quantization") or ""),
                        context_length=self._optional_int(item.get("max_context_length")),
                    )
                )
            if models:
                return models
        except ModelCatalogError as exc:
            if exc.status_code not in {404, 405}:
                raise
        return self._list_openai_compatible()

    def _list_ollama(self) -> list[ModelInfo]:
        payload = self._request_json("GET", f"{self.origin}/api/tags")
        try:
            running_payload = self._request_json("GET", f"{self.origin}/api/ps")
        except ModelCatalogError:
            running_payload = {"models": []}
        running = {
            str(item.get("name") or item.get("model")): item
            for item in running_payload.get("models", [])
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        }

        models = []
        for item in payload.get("models", []):
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("name") or item.get("model") or "").strip()
            if not model_id:
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            loaded_item = running.get(model_id)
            vision: bool | None = None
            context_length = self._optional_int((loaded_item or {}).get("context_length"))
            try:
                shown = self._request_json("POST", f"{self.origin}/api/show", json={"model": model_id})
                capabilities = shown.get("capabilities") or []
                if isinstance(capabilities, list):
                    vision = "vision" in {str(value).casefold() for value in capabilities}
                if context_length is None:
                    model_info = shown.get("model_info") or {}
                    if isinstance(model_info, dict):
                        context_length = next(
                            (
                                self._optional_int(value)
                                for key, value in model_info.items()
                                if str(key).endswith(".context_length")
                            ),
                            None,
                        )
            except ModelCatalogError:
                pass
            models.append(
                ModelInfo(
                    model_id=model_id,
                    provider=self.engine,
                    loaded=loaded_item is not None,
                    supports_vision=vision,
                    size_bytes=self._optional_int(item.get("size")),
                    quantization=str(details.get("quantization_level") or ""),
                    context_length=context_length,
                )
            )
        return models

    def _list_openai_compatible(self) -> list[ModelInfo]:
        payload = self._request_json(
            "GET",
            f"{self.base_url}/models",
            headers=self._auth_headers(),
        )
        return [
            ModelInfo(model_id=str(item["id"]), provider=self.engine)
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]

    def _request_json(self, method: str, url: str, **kwargs) -> dict:
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                message = "API key is missing or was rejected by the model server"
            else:
                message = f"Model server returned HTTP {status_code} from {url}"
            raise ModelCatalogError(message, status_code) from exc
        except httpx.HTTPError as exc:
            raise ModelCatalogError(f"Unable to read the model catalog from {url}: {exc}") from exc
        except ValueError as exc:
            raise ModelCatalogError(f"Model catalog returned invalid JSON from {url}") from exc
        if not isinstance(payload, dict):
            raise ModelCatalogError(f"Model catalog returned an invalid response from {url}")
        return payload

    def _auth_headers(self) -> dict[str, str]:
        api_key = self.api_key
        if not api_key and self.engine is EngineKind.UNSLOTH:
            api_key = os.getenv("UNSLOTH_API_KEY", "")
        if not api_key and self.engine is EngineKind.CUSTOM:
            api_key = os.getenv("OCR_AI_API_KEY", "")
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @staticmethod
    def _origin(base_url: str) -> str:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelCatalogError("Model server URL is invalid")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
