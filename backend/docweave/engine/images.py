"""Raster image sources (PNG/JPEG/TIFF/BMP/WEBP/GIF)."""
from __future__ import annotations

import io

from PIL import Image, ImageOps

from .limits import LIMITS, LimitError

Image.MAX_IMAGE_PIXELS = LIMITS.max_image_pixels


def _open(data: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Image.DecompressionBombError as e:
        raise LimitError("图片像素数量超过上限") from e
    except Exception as e:
        raise ValueError(f"图片无法打开：{e}") from e
    return im


def inspect_image(data: bytes) -> dict:
    im = _open(data)
    frames = getattr(im, "n_frames", 1)
    exif_rot = 0
    try:
        o = im.getexif().get(0x0112)
        exif_rot = {3: 180, 6: 270, 8: 90}.get(o, 0)
    except Exception:
        pass
    im2 = ImageOps.exif_transpose(im)
    return {"width": im2.width, "height": im2.height, "mode": im.mode, "format": im.format, "frames": frames,
            "exif_rotation": exif_rot}


def to_png(data: bytes, max_side: int = 4000) -> bytes:
    """Display/OCR raster: EXIF orientation applied, first frame, RGB."""
    im = ImageOps.exif_transpose(_open(data))
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()
