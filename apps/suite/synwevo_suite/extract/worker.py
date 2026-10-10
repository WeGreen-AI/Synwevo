"""Extraction-specific normalization over the shared killable parsing process."""
from typing import Protocol

from synwevo_core.errors import ServiceError
from ..common.process import TIMEOUT, run_bounded
from .definition import normalize


class Reader(Protocol):
    def describe(self) -> dict: ...
    def extract(self, data: bytes, filename: str, *, ocr: bool = False) -> dict: ...


def _read(reader, data, filename, ocr):
    description = reader.describe()
    if ocr and not description.get('ocr'):
        raise ServiceError(503, 'ocr_not_configured', '文字识别服务未配置')
    return normalize(reader.extract(data, filename, ocr=ocr), description, ocr=ocr)


def parse_bounded(reader: Reader, data: bytes, filename: str, ocr: bool, check, *, timeout=TIMEOUT) -> dict:
    return run_bounded(_read, (reader, data, filename, ocr), check, timeout=timeout)
