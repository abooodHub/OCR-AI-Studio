from unittest import TestCase

import httpx

from ocr_ai_studio.ai.model_catalog import ModelCatalogClient, ModelCatalogError
from ocr_ai_studio.domain.models import EngineKind


class ModelCatalogTests(TestCase):
    def test_lm_studio_catalog_exposes_loaded_and_vision_state(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v0/models")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "text-model",
                            "type": "llm",
                            "state": "not-loaded",
                            "quantization": "Q4_K_M",
                        },
                        {
                            "id": "vision-model",
                            "type": "vlm",
                            "state": "loaded",
                            "max_context_length": 32768,
                        },
                    ]
                },
            )

        catalog = ModelCatalogClient(
            EngineKind.LM_STUDIO,
            "http://127.0.0.1:1234/v1",
            transport=httpx.MockTransport(handler),
        ).list_models()
        self.assertEqual([item.model_id for item in catalog], ["vision-model", "text-model"])
        self.assertTrue(catalog[0].loaded)
        self.assertTrue(catalog[0].supports_vision)
        self.assertEqual(catalog[0].context_length, 32768)

    def test_ollama_catalog_merges_installed_running_and_capabilities(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "qwen3-vl:8b",
                                "size": 5_000_000_000,
                                "details": {"quantization_level": "Q4_K_M"},
                            },
                            {"name": "text:latest", "size": 2_000_000_000, "details": {}},
                        ]
                    },
                )
            if request.url.path == "/api/ps":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-vl:8b", "context_length": 8192}]},
                )
            if request.url.path == "/api/show":
                model = request.read().decode("utf-8")
                capabilities = ["vision"] if "qwen3-vl" in model else ["completion"]
                return httpx.Response(200, json={"capabilities": capabilities})
            return httpx.Response(404)

        catalog = ModelCatalogClient(
            EngineKind.OLLAMA,
            "http://127.0.0.1:11434/v1",
            transport=httpx.MockTransport(handler),
        ).list_models()
        self.assertEqual(catalog[0].model_id, "qwen3-vl:8b")
        self.assertTrue(catalog[0].loaded)
        self.assertTrue(catalog[0].supports_vision)
        self.assertFalse(catalog[1].supports_vision)

    def test_unsloth_catalog_uses_v1_models_and_bearer_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/models")
            self.assertEqual(request.headers["Authorization"], "Bearer secret")
            return httpx.Response(200, json={"data": [{"id": "unsloth/vision-gguf"}]})

        catalog = ModelCatalogClient(
            EngineKind.UNSLOTH,
            "http://127.0.0.1:8888/v1",
            api_key="secret",
            transport=httpx.MockTransport(handler),
        ).list_models()
        self.assertEqual(catalog[0].model_id, "unsloth/vision-gguf")
        self.assertIsNone(catalog[0].supports_vision)

    def test_authentication_error_has_an_actionable_message(self) -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(401, json={"error": "no"}))
        client = ModelCatalogClient(
            EngineKind.UNSLOTH,
            "http://127.0.0.1:8888/v1",
            transport=transport,
        )
        with self.assertRaisesRegex(ModelCatalogError, "API key"):
            client.list_models()
