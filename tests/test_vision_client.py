from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ocr_ai_studio.ai.vision_client import OCRRequestError, VisionClient
from ocr_ai_studio.domain.models import EngineKind


class VisionClientTests(TestCase):
    @patch("ocr_ai_studio.ai.vision_client.OpenAI")
    def test_unsloth_uses_openai_compatible_endpoint_and_api_key(self, openai_mock) -> None:
        VisionClient(
            EngineKind.UNSLOTH,
            "http://127.0.0.1:8888/v1",
            "unsloth/vision-model",
            api_key="secret-token",
        )
        kwargs = openai_mock.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "http://127.0.0.1:8888/v1")
        self.assertEqual(kwargs["api_key"], "secret-token")

    @patch("ocr_ai_studio.ai.vision_client.OpenAI")
    def test_blank_response_is_an_error_not_an_empty_subtitle(self, openai_mock) -> None:
        sdk_client = openai_mock.return_value
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
        )
        client = VisionClient(
            EngineKind.LM_STUDIO,
            "http://127.0.0.1:1234/v1",
            "vision-model",
            max_retries=1,
        )
        with self.assertRaisesRegex(OCRRequestError, "empty or invalid"):
            client.ocr(b"image")

    @patch("ocr_ai_studio.ai.vision_client.OpenAI")
    def test_explicit_empty_marker_is_a_valid_empty_subtitle(self, openai_mock) -> None:
        sdk_client = openai_mock.return_value
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="EMPTY"))]
        )
        client = VisionClient(
            EngineKind.OLLAMA,
            "http://127.0.0.1:11434/v1",
            "vision-model",
            max_retries=1,
        )
        self.assertEqual(client.ocr(b"image"), "")

    @patch("ocr_ai_studio.ai.vision_client.OpenAI")
    def test_language_metadata_is_passed_as_recognition_hint_only(self, openai_mock) -> None:
        sdk_client = openai_mock.return_value
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="مرحبا"))]
        )
        client = VisionClient(
            EngineKind.LM_STUDIO,
            "http://127.0.0.1:1234/v1",
            "vision-model",
            max_retries=1,
            language_hint="ara",
        )

        client.ocr(b"image")

        messages = sdk_client.chat.completions.create.call_args.kwargs["messages"]
        instruction = messages[1]["content"][1]["text"]
        self.assertIn("ara", instruction)
        self.assertIn("never to translate", instruction)
