import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import httpx

from ocr_ai_studio.ai.runtime_manager import (
    EngineRuntimeError,
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
            configured_executable=__file__,
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
            configured_executable=__file__,
        )

        self.assertEqual(snapshot.state, RuntimeState.ERROR)
        self.assertTrue(snapshot.reachable)
        self.assertIn("API", snapshot.detail)

    def test_remote_server_cannot_be_started_by_the_application(self) -> None:
        manager = EngineRuntimeManager()

        with self.assertRaises(EngineRuntimeError):
            manager.start(
                EngineKind.LM_STUDIO,
                "http://192.168.1.30:1234/v1",
                "vision-model",
                configured_executable=__file__,
            )

    def test_ollama_start_uses_argument_list_and_loopback_environment(self) -> None:
        with TemporaryDirectory() as directory:
            executable = Path(directory) / "ollama.exe"
            executable.touch()
            process = Mock(spec=subprocess.Popen)
            process.poll.return_value = None
            with patch("ocr_ai_studio.ai.runtime_manager.subprocess.Popen", return_value=process) as popen:
                manager = EngineRuntimeManager()
                snapshot = manager.start(
                    EngineKind.OLLAMA,
                    "http://127.0.0.1:11434/v1",
                    "qwen3-vl:8b",
                    configured_executable=str(executable),
                )

        self.assertEqual(snapshot.state, RuntimeState.STARTING)
        self.assertTrue(snapshot.owned)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(executable.resolve()), "serve"])
        self.assertEqual(kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertNotIn("shell", kwargs)

    def test_external_runtime_is_never_stopped(self) -> None:
        manager = EngineRuntimeManager()
        self.assertFalse(manager.stop_owned(EngineKind.LM_STUDIO, __file__))
