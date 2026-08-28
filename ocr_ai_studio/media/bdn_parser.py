from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image


class BDNError(ValueError):
    pass


class BDNParser:
    """Read Blu-ray BDN XML image subtitles without changing their timecodes."""

    def __init__(self, xml_path: Path) -> None:
        self.xml_path = xml_path
        try:
            self.root = ElementTree.parse(xml_path).getroot()
        except (ElementTree.ParseError, OSError) as exc:
            raise BDNError(f"Invalid BDN XML file: {xml_path}") from exc
        self.frame_rate = self._frame_rate()
        self.events = [element for element in self.root.iter() if self._tag(element) == "Event"]
        if not self.events or not any(
            self._tag(child) == "Graphic" for event in self.events for child in event
        ):
            raise BDNError("XML file is not a BDN subtitle project with graphic events")

    @staticmethod
    def _tag(element: ElementTree.Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    def _frame_rate(self) -> float:
        for element in self.root.iter():
            value = element.attrib.get("FrameRate") or element.attrib.get("frameRate")
            if value:
                try:
                    rate = float(value)
                except ValueError as exc:
                    raise BDNError(f"Invalid BDN frame rate: {value}") from exc
                if rate > 0:
                    return rate
        return 24.0

    def count_events(self) -> int:
        return len(self.events)

    def graphic_paths(self) -> list[Path]:
        paths = []
        for event in self.events:
            for graphic in event:
                if self._tag(graphic) == "Graphic" and (graphic.text or "").strip():
                    paths.append((self.xml_path.parent / (graphic.text or "").strip()).resolve())
        return paths

    def parse(self) -> Iterator[tuple[int, int, Image.Image]]:
        for event in self.events:
            start = event.attrib.get("InTC") or event.attrib.get("inTC")
            end = event.attrib.get("OutTC") or event.attrib.get("outTC")
            if not start or not end:
                raise BDNError("BDN event is missing InTC or OutTC")
            start_ms = self._timecode_ms(start)
            end_ms = self._timecode_ms(end)
            if end_ms <= start_ms:
                raise BDNError(f"BDN event has an invalid time range: {start} -> {end}")
            graphics = [child for child in event if self._tag(child) == "Graphic"]
            image = self._compose_graphics(graphics)
            yield start_ms, end_ms, image

    def _timecode_ms(self, value: str) -> int:
        parts = value.replace(";", ":").split(":")
        if len(parts) != 4:
            raise BDNError(f"Invalid BDN timecode: {value}")
        try:
            hours, minutes, seconds, frames = (int(part) for part in parts)
        except ValueError as exc:
            raise BDNError(f"Invalid BDN timecode: {value}") from exc
        if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60 or frames < 0:
            raise BDNError(f"Invalid BDN timecode: {value}")
        return round((hours * 3_600 + minutes * 60 + seconds + frames / self.frame_rate) * 1_000)

    def _compose_graphics(self, graphics: list[ElementTree.Element]) -> Image.Image:
        rendered: list[tuple[int, int, Image.Image]] = []
        for graphic in graphics:
            relative = (graphic.text or "").strip()
            if not relative:
                continue
            image_path = (self.xml_path.parent / relative).resolve()
            if not image_path.is_file():
                raise BDNError(f"BDN graphic image was not found: {relative}")
            try:
                with Image.open(image_path) as source:
                    image = source.convert("RGBA")
            except OSError as exc:
                raise BDNError(f"BDN graphic image is invalid: {relative}") from exc
            x = int(graphic.attrib.get("X", graphic.attrib.get("x", 0)))
            y = int(graphic.attrib.get("Y", graphic.attrib.get("y", 0)))
            rendered.append((x, y, image))
        if not rendered:
            raise BDNError("BDN event does not contain a readable graphic image")
        if len(rendered) == 1:
            return rendered[0][2]
        min_x = min(item[0] for item in rendered)
        min_y = min(item[1] for item in rendered)
        max_x = max(x + image.width for x, _y, image in rendered)
        max_y = max(y + image.height for _x, y, image in rendered)
        canvas = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
        for x, y, image in rendered:
            canvas.alpha_composite(image, (x - min_x, y - min_y))
        return canvas
