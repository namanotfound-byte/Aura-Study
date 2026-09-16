#!/usr/bin/env python3
"""Build multi-size favicon.ico and PNGs from aurastudy-icon-512.png."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "static" / "brand"
SOURCE = BRAND / "aurastudy-icon-512.png"
SIZES = (16, 32, 48)


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", crc)


def image_to_png_bytes(img: Image.Image) -> bytes:
    rgba = img.convert("RGBA")
    w, h = rgba.size
    raw = b"".join(
        b"\x00" + rgba.tobytes()[y * w * 4 : (y + 1) * w * 4]
        for y in range(h)
    )
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )


def build_ico(images: dict[int, Image.Image]) -> bytes:
    entries = []
    image_data = []
    offset = 6 + 16 * len(images)
    for size in sorted(images):
        png = image_to_png_bytes(images[size])
        entries.append((size, len(png), offset))
        image_data.append(png)
        offset += len(png)
    header = struct.pack("<HHH", 0, 1, len(entries))
    dir_bytes = b""
    for size, length, off in entries:
        dir_bytes += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            length,
            off,
        )
    return header + dir_bytes + b"".join(image_data)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source icon: {SOURCE}")

    src = Image.open(SOURCE).convert("RGBA")
    resized: dict[int, Image.Image] = {}
    for size in SIZES:
        img = src.resize((size, size), Image.Resampling.LANCZOS)
        resized[size] = img
        out = BRAND / f"favicon-{size}.png"
        img.save(out, format="PNG", optimize=True)
        print(f"wrote {out} ({out.stat().st_size} bytes)")

    ico_path = BRAND / "favicon.ico"
    ico_path.write_bytes(build_ico(resized))
    print(f"wrote {ico_path} ({ico_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
