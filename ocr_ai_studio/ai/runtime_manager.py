from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

import httpx

from ocr_ai_studio.domain.models import EngineKind


class RuntimeState(str, Enum):
    NOT_INSTALLED = "not_installed"
    STOPPED = "stopped"
    STARTING = "starting"
    ONLINE = "online"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    engine: EngineKind
    state: RuntimeState
    executable: str = ""
    detail: str = ""
    reachable: bool = False
    owned: bool = False


class EngineRuntimeError(RuntimeError):
    """The selected local inference runtime could not be managed safely."""


class EngineRuntimeManager:
    """Probe an already-running inference server without managing external processes."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def inspect(
        self,
        engine: EngineKind,
        base_url: str,
        *,
        api_key: str = "",
        probe: bool = True,
    ) -> RuntimeSnapshot:
        if not probe:
            return RuntimeSnapshot(
                engine,
                RuntimeState.STOPPED,
                "",
                "لم يتم فحص اتصال API بعد — شغّل المحرك يدويًا ثم افحص الجاهزية",
            )
        return self._probe(engine, base_url, api_key)

    def _probe(
        self,
        engine: EngineKind,
        base_url: str,
        api_key: str,
    ) -> RuntimeSnapshot:
        try:
            origin = self._origin(base_url)
        except EngineRuntimeError as exc:
            return RuntimeSnapshot(engine, RuntimeState.ERROR, detail=str(exc))
        endpoints = {
            EngineKind.LM_STUDIO: (f"{origin}/api/v0/models", f"{origin}/v1/models"),
            EngineKind.OLLAMA: (f"{origin}/api/tags",),
            EngineKind.UNSLOTH: (f"{origin}/api/health", f"{origin}/v1/models"),
            EngineKind.CUSTOM: (f"{base_url.rstrip('/')}/models",),
        }[engine]
        headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key.strip() else {}
        last_error = "الخادم لا يستجيب"
        try:
            with httpx.Client(
                timeout=2.5,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                for endpoint in endpoints:
                    try:
                        response = client.get(endpoint, headers=headers)
                    except (httpx.HTTPError, OSError) as exc:
                        last_error = self._connection_error(exc)
                        continue
                    if 200 <= response.status_code < 300:
                        return RuntimeSnapshot(
                            engine,
                            RuntimeState.ONLINE,
                            "",
                            "الخادم متصل ويستجيب للطلبات",
                            reachable=True,
                        )
                    if response.status_code in {401, 403}:
                        return RuntimeSnapshot(
                            engine,
                            RuntimeState.ERROR,
                            "",
                            "الخادم يعمل لكنه يحتاج مفتاح API صحيحًا",
                            reachable=True,
                        )
                    last_error = f"استجابة HTTP {response.status_code}"
        except (httpx.HTTPError, OSError) as exc:
            last_error = self._connection_error(exc)
        return RuntimeSnapshot(
            engine,
            RuntimeState.STOPPED,
            "",
            f"الخادم متوقف أو غير متاح: {last_error}",
        )

    @staticmethod
    def _connection_error(error: Exception) -> str:
        if isinstance(error, FileNotFoundError):
            return "تعذر الوصول إلى خدمة المحرك المحلية"
        return str(error)

    @staticmethod
    def _origin(base_url: str) -> str:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EngineRuntimeError("عنوان الخادم غير صالح")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
