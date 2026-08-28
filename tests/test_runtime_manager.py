from unittest import TestCase

import httpx

from ocr_ai_studio.ai.runtime_manager import (
    EngineRuntimeManager,
    RuntimeState,
)
from ocr_ai_studio.domain.models import EngineKind


class RuntimeManagerTests(TestCase):
    def test_provider_probe_uses_provider_specific_health_endpoint(self) -> None:
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            return httpx.Response(200, json={"models": []})

        manager = EngineRuntimeManager(httpx.MockTransport(handler))
        snapshot = manager.inspect(
            EngineKind.OLLAMA,
            "http://127.0.0.1:11434/v1",
        )

        self.assertEqual(snapshot.state, RuntimeState.ONLINE)
        self.assertTrue(snapshot.reachable)
        self.assertEqual(requested_paths, ["/api/tags"])

    def test_authentication_failure_is_reachable_but_not_ready(self) -> None:
        manager = EngineRuntimeManager(
            httpx.MockTransport(lambda _request: httpx.Response(401, json={"error": "unauthorized"}))
        )

        snapshot = manager.inspect(
            EngineKind.UNSLOTH,
            "http://127.0.0.1:8888/v1",
        )

        self.assertEqual(snapshot.state, RuntimeState.ERROR)
        self.assertTrue(snapshot.reachable)
        self.assertIn("API", snapshot.detail)

    def test_missing_local_service_is_reported_as_stopped_not_raised(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise FileNotFoundError(2, "No such file or directory")

        manager = EngineRuntimeManager(httpx.MockTransport(handler))

        snapshot = manager.inspect(
            EngineKind.LM_STUDIO,
            "http://127.0.0.1:1234/v1",
        )

        self.assertEqual(snapshot.state, RuntimeState.STOPPED)
        self.assertIn("خدمة المحرك المحلية", snapshot.detail)

    def test_runtime_manager_exposes_no_process_management_api(self) -> None:
        manager = EngineRuntimeManager()
        self.assertFalse(hasattr(manager, "start"))
        self.assertFalse(hasattr(manager, "stop_owned"))
        self.assertFalse(hasattr(manager, "shutdown_owned"))
