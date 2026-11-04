"""Speech and segmentation results, and the stand-in client that reports them unavailable."""

from __future__ import annotations

import re
from dataclasses import dataclass

from crewpay.models.client import ModelUnavailable
from crewpay.settings import Settings

BOX = re.compile(r"<\|box;([^|]*)\|>")


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    width: int
    """Of the whole image, in pixels."""
    height: int
    confidence: float

    @property
    def share(self) -> float:
        """How much of the image the box covers, 0 to 1."""
        return (self.x2 - self.x1) * (self.y2 - self.y1) / max(1, self.width * self.height)


def parse_boxes(text: str) -> list[Box]:
    boxes = []
    for raw in BOX.findall(text):
        fields = dict(part.split("=", 1) for part in raw.split(";") if "=" in part)
        try:
            boxes.append(
                Box(
                    int(fields["x1"]),
                    int(fields["y1"]),
                    int(fields["x2"]),
                    int(fields["y2"]),
                    int(fields["w"]),
                    int(fields["h"]),
                    float(fields.get("c", 0)),
                )
            )
        except (KeyError, ValueError):
            continue
    return boxes


class MediaClient:
    """Speech and image segmentation. CrewPay runs without them: spoken questions are typed,
    and photos are read for their fields without a damage box. Each call says so."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config

    def transcribe(self, wav: bytes, languages: list[str] | None = None) -> Transcript:
        raise ModelUnavailable("speech is not part of CrewPay; type the question instead")

    def segment(self, image: bytes, mime: str, phrase: str) -> list[Box]:
        raise ModelUnavailable("image segmentation is not part of CrewPay")
