"""The platform's avatar image rules, in one place (moved here from the account service in CI-78
so a group avatar in Synwevo Message is held to exactly what a person's and an organization's
avatar are held to, without an application importing the account service).

Accepted: a bounded, non-interlaced 8-bit PNG (greyscale, RGB, grey+alpha or RGBA), at most
256 KB and 512 × 512 pixels. The header is sniffed from the bytes, every chunk's CRC checked, the
inflated scanlines bounded before any pixel is accepted, and the image is *rebuilt* from IHDR/IDAT/
IEND only — metadata, animation (APNG), unknown critical chunks, trailing bytes, SVG and every
other format are refused. No image library: the standard library's zlib and struct are enough.
"""
from __future__ import annotations

import base64
import binascii
import struct
import zlib

from .errors import ServiceError

MAX_AVATAR_BYTES = 256 * 1024
MAX_AVATAR_SIDE = 512


def canonical_avatar(encoded: str) -> bytes:
    """Accept bounded, non-interlaced 8-bit PNG; rebuild only IHDR/IDAT/IEND.

    No SVG, animation, filenames, URLs, external fetches, metadata or new image dependency.
    Bound compressed input AND inflated scanlines before accepting any pixel data.
    """
    def bad():
        return ServiceError(400, "bad_avatar", "请上传不超过 256 KB、512 × 512 像素的普通 PNG 图片")

    if not isinstance(encoded, str) or len(encoded) > (MAX_AVATAR_BYTES * 4 // 3 + 4):
        raise bad()
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_AVATAR_BYTES or raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError()
        pos, header, compressed, ended, after_idat = 8, None, bytearray(), False, False
        while pos < len(raw):
            length = struct.unpack(">I", raw[pos:pos + 4])[0]
            kind = raw[pos + 4:pos + 8]
            payload = raw[pos + 8:pos + 8 + length]
            end = pos + 12 + length
            if end > len(raw) or len(kind) != 4:
                raise ValueError()
            crc = struct.unpack(">I", raw[pos + 8 + length:end])[0]
            if binascii.crc32(kind + payload) & 0xffffffff != crc:
                raise ValueError()
            if header is None:
                if kind != b"IHDR" or length != 13:
                    raise ValueError()
                header = payload
            elif kind == b"IDAT":
                if after_idat:
                    raise ValueError()
                compressed.extend(payload)
            elif kind == b"IEND":
                if length or end != len(raw):
                    raise ValueError()
                ended = True
            else:
                # Unknown critical chunks and APNG are rejected; ancillary metadata is stripped.
                if not kind[0] & 32 or kind in (b"acTL", b"fcTL", b"fdAT"):
                    raise ValueError()
                if compressed:
                    after_idat = True
            pos = end
        if not ended or not header or not compressed:
            raise ValueError()
        width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
        if (not channels or not 1 <= width <= MAX_AVATAR_SIDE or not 1 <= height <= MAX_AVATAR_SIDE
                or depth != 8 or compression or filtering or interlace):
            raise ValueError()
        stride = 1 + width * channels
        expected = height * stride
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), expected + 1)
        if (len(pixels) != expected or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail or any(pixels[i] > 4 for i in range(0, expected, stride))):
            raise ValueError()
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
        return raw[:8] + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
    except (ValueError, struct.error, zlib.error, binascii.Error):
        raise bad() from None
