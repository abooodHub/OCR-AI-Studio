import io
from unittest import TestCase

from PIL import Image, ImageDraw

from ocr_ai_studio.processing.image_processor import ImageProcessor


class ImageProcessorTests(TestCase):
    def test_small_pgs_subtitle_is_upscaled_and_kept_lossless_enough_for_ocr(self) -> None:
        source = Image.new("RGBA", (220, 42), (0, 0, 0, 0))
        ImageDraw.Draw(source).rectangle((20, 10, 200, 32), fill=(255, 255, 255, 255))

        prepared = ImageProcessor().prepare(source)

        self.assertIsNotNone(prepared)
        rendered = Image.open(io.BytesIO(prepared))
        self.assertGreater(rendered.width, 220)
        self.assertGreater(rendered.height, 42)

    def test_fully_transparent_pgs_frame_is_skipped(self) -> None:
        source = Image.new("RGBA", (220, 42), (0, 0, 0, 0))
        self.assertIsNone(ImageProcessor().prepare(source))
