"""Notebook displays for real, paused ONI screenshots (no graphics dependency)."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_FRAME_BYTES = 16 * 1024 * 1024


def png_size(data: bytes) -> tuple[int, int]:
    """Reject incomplete/corrupt captures before displaying or saving them."""
    if not data.startswith(PNG_SIGNATURE) or len(data) > MAX_FRAME_BYTES:
        raise ValueError("Expected a PNG screenshot no larger than 16 MiB")
    offset = 8
    size = None
    has_pixels = False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            break
        kind = data[offset + 4 : offset + 8]
        content = data[offset + 8 : end - 4]
        checksum = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + content) & 0xFFFFFFFF != checksum:
            raise ValueError("PNG screenshot has an invalid checksum")
        if size is None:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG screenshot has no valid image header")
            size = struct.unpack_from(">II", content)
            if min(size) < 1 or size[0] * size[1] > 50_000_000:
                raise ValueError("PNG screenshot dimensions exceed the display limit")
        if kind == b"IEND":
            if length or end != len(data) or not has_pixels:
                raise ValueError("PNG screenshot has an invalid ending")
            return size
        has_pixels |= kind == b"IDAT"
        offset = end
    raise ValueError("PNG screenshot is incomplete")


@dataclass(frozen=True, repr=False)
class GameFrame:
    """An immutable screenshot, embedded directly in saved notebook outputs.

    This is the game's rendered viewport, not a synthetic map. Capturing needs
    graphics-enabled ONI and OniMcp on the same machine as the Python kernel.
    ``captured_at`` is the client receipt time in UTC, not a simulation clock.
    """

    png: bytes
    captured_at: str
    cycle: float | None = None

    def __post_init__(self) -> None:
        png_size(self.png)

    @property
    def size(self) -> tuple[int, int]:
        return png_size(self.png)

    def _repr_png_(self) -> bytes:
        return self.png

    def __repr__(self) -> str:
        width, height = self.size
        return (
            f"GameFrame({width}x{height}, cycle={self.cycle}, "
            f"received={self.captured_at}, paused=True)"
        )

    def save(self, path: str | Path) -> Path:
        """Save the original PNG bytes, without resizing or changing game art."""
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.png)
        return target
