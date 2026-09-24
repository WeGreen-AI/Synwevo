"""Raster image sources (PNG/JPEG/TIFF/BMP/WEBP/GIF)."""
from __future__ import annotations

import io

from PIL import Image, ImageOps

from .limits import LIMITS, LimitError

Image.MAX_IMAGE_PIXELS = LIMITS.max_image_pixels


def _too_many_pixels(w: int, h: int) -> LimitError:
    return LimitError(f"图片像素 {w}×{h} 超过上限（{LIMITS.max_image_pixels // 1_000_000} MP，可通过 SYNWEVO_DOCS_MAX_IMAGE_MPIX 调整）")


def _open(data: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as e:
        raise LimitError("图片像素数量超过上限") from e
    except Exception as e:
        raise ValueError(f"图片无法打开：{e}") from e
    # Pillow only refuses above twice MAX_IMAGE_PIXELS (between 1× and 2× it merely warns),
    # so the documented limit is checked here, from the header, before anything is decoded
    if im.width * im.height > LIMITS.max_image_pixels:
        raise _too_many_pixels(im.width, im.height)
    try:
        im.load()
    except Image.DecompressionBombError as e:
        raise LimitError("图片像素数量超过上限") from e
    except Exception as e:
        raise ValueError(f"图片无法打开：{e}") from e
    return im


def inspect_image(data: bytes) -> dict:
    im = _open(data)
    # n_frames decodes every frame of a GIF to count them; is_animated stops at the second
    animated = bool(getattr(im, "is_animated", False))
    exif_rot = 0
    try:
        o = im.getexif().get(0x0112)
        exif_rot = {3: 180, 6: 270, 8: 90}.get(o, 0)
    except Exception:
        pass
    im2 = ImageOps.exif_transpose(im)
    return {"width": im2.width, "height": im2.height, "mode": im.mode, "format": im.format, "animated": animated,
            "exif_rotation": exif_rot}


def to_png(data: bytes, max_side: int = 4000) -> bytes:
    """Display/OCR raster: EXIF orientation applied, first frame, RGB."""
    src = _open(data)
    if getattr(src, "is_animated", False):
        src.seek(0)
    im = ImageOps.exif_transpose(src)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()
