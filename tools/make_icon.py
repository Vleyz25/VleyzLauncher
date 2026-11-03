"""Генерация assets/vleyz.ico без сторонних библиотек (32x32 BGRA + 16x16)."""
from __future__ import annotations

import struct
from pathlib import Path


def blend(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


GREEN = (74, 222, 128)
DARK = (10, 14, 21)


def pixel(x, y, size):
    """Простой изометрический блок в квадрате size x size."""
    cx = cy = size / 2
    half = size * 0.36
    quarter = size * 0.18
    fx, fy = x + 0.5 - cx, y + 0.5 - cy

    # верхняя грань: ромб
    if abs(fx) / half + abs(fy + quarter) / quarter <= 1 and fy <= 0:
        return (*blend(GREEN, (255, 255, 255), 0.35), 255)
    # левая грань
    if -half <= fx <= 0 and 0 <= fy <= half * 0.85:
        return (*blend(GREEN, (0, 0, 0), 0.32), 255)
    # правая грань
    if 0 <= fx <= half and 0 <= fy <= half * 0.85:
        return (*blend(GREEN, (0, 0, 0), 0.12), 255)
    return DARK + (0,)


def render(size: int) -> bytes:
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            r, g, b, a = pixel(x, y, size)
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    pixels = b"".join(rows)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    return header + pixels


def build(path: Path) -> None:
    sizes = [16, 32, 48, 64]
    images = [render(s) for s in sizes]
    offset = 6 + 16 * len(sizes)
    directory = b""
    for size, image in zip(sizes, images):
        directory += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + directory + b"".join(images))
    print(f"OK {path} ({path.stat().st_size} байт)")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1] / "assets" / "vleyz.ico")
