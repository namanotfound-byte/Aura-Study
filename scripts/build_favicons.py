#!/usr/bin/env python3
"""Build transparent favicons and PWA icons from AuraStudyLogo_nobg.png."""
from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "static" / "brand"
SOURCE = ROOT / "AuraStudyLogo_nobg.png"
WORDMARK_SOURCE = ROOT / "AuraStudyLogotext_nobg.png"

FAVICON_SIZES = (16, 32, 48)
ICON_SIZES = {
    "apple-touch-icon.png": 180,
    "aurastudy-icon-192.png": 192,
    "aurastudy-icon-512.png": 512,
}
PADDING_RATIO = 0.08
ALPHA_CUTOFF = 24
WHITE_KNOCKOUT = 248


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


def knock_out_near_white(img: Image.Image) -> Image.Image:
    """If the source has a flat white/near-white plate, make it transparent."""
    rgba = img.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if r >= WHITE_KNOCKOUT and g >= WHITE_KNOCKOUT and b >= WHITE_KNOCKOUT:
                px[x, y] = (r, g, b, 0)
    return rgba


def content_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    alpha = img.split()[3]
    return alpha.point(lambda v: 255 if v > ALPHA_CUTOFF else 0).getbbox() or (0, 0, *img.size)


def fit_on_square(img: Image.Image, size: int, padding_ratio: float = PADDING_RATIO) -> Image.Image:
    cropped = img.crop(content_bbox(img))
    pad = max(1, int(size * padding_ratio))
    inner = size - pad * 2
    cw, ch = cropped.size
    scale = min(inner / cw, inner / ch)
    nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
    resized = cropped.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2), resized)
    return canvas


def save_brand_assets() -> Image.Image:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source mascot: {SOURCE}")

    mascot_src = knock_out_near_white(Image.open(SOURCE))
    mascot_out = BRAND / "aurastudy-mascot.png"
    mascot_src.save(mascot_out, format="PNG", optimize=True)
    print(f"wrote {mascot_out} ({mascot_out.stat().st_size} bytes)")

    if WORDMARK_SOURCE.exists():
        wordmark_out = BRAND / "aurastudy-wordmark.png"
        shutil.copy2(WORDMARK_SOURCE, wordmark_out)
        print(f"wrote {wordmark_out} ({wordmark_out.stat().st_size} bytes)")

    return mascot_src


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    src = save_brand_assets()

    resized: dict[int, Image.Image] = {}
    for size in FAVICON_SIZES:
        img = fit_on_square(src, size)
        resized[size] = img
        out = BRAND / f"favicon-{size}.png"
        img.save(out, format="PNG", optimize=True)
        corner_alpha = img.getpixel((0, 0))[3]
        print(f"wrote {out} ({out.stat().st_size} bytes) corner_alpha={corner_alpha}")

    ico_path = BRAND / "favicon.ico"
    ico_path.write_bytes(build_ico(resized))
    print(f"wrote {ico_path} ({ico_path.stat().st_size} bytes)")

    for filename, size in ICON_SIZES.items():
        img = fit_on_square(src, size)
        out = BRAND / filename
        img.save(out, format="PNG", optimize=True)
        corner_alpha = img.getpixel((0, 0))[3]
        print(f"wrote {out} ({out.stat().st_size} bytes) corner_alpha={corner_alpha}")


if __name__ == "__main__":
    main()
