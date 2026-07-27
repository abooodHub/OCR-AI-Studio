"""
image_processor.py — معمل الصور
يقتل الشفافية، يقص الفراغ، ويتحقق إن الصورة فيها نص حقيقي.
يدعم مسار خاص لـ VobSub يضخّم الصورة ويعزز التباين قبل الـ OCR.
"""

import io
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps


class ImageProcessor:
    """Prepares PGS / VobSub bitmap frames for OCR."""

    # Background colour for alpha replacement (dark grey → white text pops)
    BG_COLOR: tuple[int, int, int] = (30, 30, 30)

    # A pixel must differ from BG by at least this much to be "content"
    BLANK_THRESHOLD: int = 15

    # Pixels of padding added around the detected bounding box
    CROP_PADDING: int = 6

    # JPEG encode quality sent to the AI (smaller = faster upload)
    JPEG_QUALITY: int = 90

    # Minimum meaningful frame size (pixels)
    MIN_SIZE: int = 8

    # VobSub: minimum long-side length before we upscale
    VOBSUB_MIN_LONG_SIDE: int = 300

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, pil_img: Image.Image) -> bytes | None:
        """
        Full pipeline for PGS frames:
          1. Kill transparency → dark background
          2. Detect blank → return None
          3. Auto-crop to content
          4. Detect blank again → return None
          5. Encode as JPEG bytes

        Returns None for empty/transparent frames (saves API calls).
        """
        if pil_img is None:
            return None

        img = self.kill_alpha(pil_img)

        if self.is_blank(img):
            return None

        img = self.auto_crop(img)
        if img is None or img.width < self.MIN_SIZE or img.height < self.MIN_SIZE:
            return None

        if self.is_blank(img):
            return None

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=self.JPEG_QUALITY, optimize=True)
        return buf.getvalue()

    def prepare_vobsub(self, pil_img: Image.Image) -> bytes | None:
        """
        Enhanced pipeline for VobSub bitmap frames.
        VobSub images are often small (100–200 px wide) with muted colors.
        Extra steps:
          1. Kill alpha → white background (better contrast for pale text)
          2. Detect blank → skip
          3. Auto-crop
          4. Upscale if too small  (long side < VOBSUB_MIN_LONG_SIDE)
          5. Sharpen + boost contrast
          6. Encode as high-quality JPEG
        """
        if pil_img is None:
            return None

        img = self._kill_alpha_white(pil_img)

        if self._is_blank_white(img):
            return None

        img = self._auto_crop_white(img)
        if img is None or img.width < self.MIN_SIZE or img.height < self.MIN_SIZE:
            return None

        if self._is_blank_white(img):
            return None

        # ── Upscale tiny images ───────────────────────────────────────
        long_side = max(img.width, img.height)
        if long_side < self.VOBSUB_MIN_LONG_SIDE:
            scale = self.VOBSUB_MIN_LONG_SIDE / long_side
            new_w = max(int(img.width  * scale), 1)
            new_h = max(int(img.height * scale), 1)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # ── Sharpen + contrast boost ──────────────────────────────────
        img = img.filter(ImageFilter.SHARPEN)
        img = ImageEnhance.Contrast(img).enhance(1.8)
        img = ImageEnhance.Sharpness(img).enhance(2.0)

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=95, optimize=True)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Steps  (PGS — dark background)
    # ------------------------------------------------------------------

    def kill_alpha(self, img: Image.Image) -> Image.Image:
        """
        Replace any alpha channel with a solid dark-grey background.
        White subtitle text stays bright; transparent areas go dark.
        """
        if img.mode == "RGBA":
            background = Image.new("RGBA", img.size, (*self.BG_COLOR, 255))
            background.alpha_composite(img)
            return background.convert("RGB")
        if img.mode not in ("RGB",):
            return img.convert("RGB")
        return img

    def is_blank(self, img: Image.Image) -> bool:
        """
        Return True if the image is indistinguishable from a plain background.
        Uses ImageChops.difference against the background colour.
        """
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        bg = Image.new("RGB", rgb.size, self.BG_COLOR)
        diff = ImageChops.difference(rgb, bg)
        bbox = diff.getbbox()
        if bbox is None:
            return True
        extrema = diff.getextrema()
        max_diff = max(ch[1] for ch in extrema)
        return max_diff < self.BLANK_THRESHOLD

    def auto_crop(self, img: Image.Image) -> Image.Image | None:
        """
        Crop to the bounding box of non-background pixels, with padding.
        Returns None if nothing to crop to.
        """
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        bg = Image.new("RGB", rgb.size, self.BG_COLOR)
        diff = ImageChops.difference(rgb, bg)
        bbox = diff.getbbox()

        if bbox is None:
            return None

        x1 = max(0, bbox[0] - self.CROP_PADDING)
        y1 = max(0, bbox[1] - self.CROP_PADDING)
        x2 = min(img.width,  bbox[2] + self.CROP_PADDING)
        y2 = min(img.height, bbox[3] + self.CROP_PADDING)

        return img.crop((x1, y1, x2, y2))

    # ------------------------------------------------------------------
    # Steps  (VobSub — white background variants)
    # ------------------------------------------------------------------

    _WHITE_BG: tuple[int, int, int] = (255, 255, 255)
    _WHITE_THRESHOLD: int = 30       # pixels this close to white = blank

    def _kill_alpha_white(self, img: Image.Image) -> Image.Image:
        """Composite RGBA image onto a white background."""
        if img.mode == "RGBA":
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            background.alpha_composite(img)
            return background.convert("RGB")
        if img.mode != "RGB":
            return img.convert("RGB")
        return img

    def _is_blank_white(self, img: Image.Image) -> bool:
        """True if the image is basically all white (no text)."""
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        bg  = Image.new("RGB", rgb.size, self._WHITE_BG)
        diff = ImageChops.difference(rgb, bg)
        bbox = diff.getbbox()
        if bbox is None:
            return True
        extrema = diff.getextrema()
        max_diff = max(ch[1] for ch in extrema)
        return max_diff < self._WHITE_THRESHOLD

    def _auto_crop_white(self, img: Image.Image) -> Image.Image | None:
        """Crop to non-white bounding box with padding."""
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        bg  = Image.new("RGB", rgb.size, self._WHITE_BG)
        diff = ImageChops.difference(rgb, bg)
        bbox = diff.getbbox()

        if bbox is None:
            return None

        x1 = max(0, bbox[0] - self.CROP_PADDING)
        y1 = max(0, bbox[1] - self.CROP_PADDING)
        x2 = min(img.width,  bbox[2] + self.CROP_PADDING)
        y2 = min(img.height, bbox[3] + self.CROP_PADDING)

        return img.crop((x1, y1, x2, y2))
