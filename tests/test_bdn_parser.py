from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from PIL import Image

from ocr_ai_studio.media.bdn_parser import BDNError, BDNParser
from ocr_ai_studio.media.ffmpeg import FFmpegService


class BDNParserTests(TestCase):
    @staticmethod
    def _write_project(root: Path) -> Path:
        Image.new("RGBA", (20, 10), (255, 255, 255, 255)).save(root / "line-one.png")
        Image.new("RGBA", (10, 8), (255, 0, 0, 255)).save(root / "line-two.png")
        xml = root / "subtitle.xml"
        xml.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<BDN Version="0.93">
  <Description><Format FrameRate="24" /></Description>
  <Events>
    <Event InTC="00:00:01:12" OutTC="00:00:03:00">
      <Graphic X="100" Y="800">line-one.png</Graphic>
      <Graphic X="105" Y="812">line-two.png</Graphic>
    </Event>
  </Events>
</BDN>
""",
            encoding="utf-8",
        )
        return xml

    def test_bdn_timecodes_and_multiple_graphics_are_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            source = self._write_project(Path(directory))
            parser = BDNParser(source)

            frames = list(parser.parse())

            self.assertEqual(parser.count_events(), 1)
            self.assertEqual(frames[0][0:2], (1_500, 3_000))
            self.assertEqual(frames[0][2].size, (20, 20))

    def test_bdn_xml_is_detected_without_ffprobe(self) -> None:
        with TemporaryDirectory() as directory:
            source = self._write_project(Path(directory))
            stream = FFmpegService(ffprobe="definitely-missing").probe_subtitles(source)[0]
            self.assertEqual(stream.codec, "bdn_xml")
            self.assertTrue(stream.is_bitmap)

    def test_non_bdn_xml_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "other.xml"
            source.write_text("<root><item /></root>", encoding="utf-8")
            with self.assertRaises(BDNError):
                BDNParser(source)
