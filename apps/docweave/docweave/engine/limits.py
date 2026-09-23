"""Processing limits.  Values come from environment variables so a deployment
can tune them; defaults are sized for a single workstation."""
from __future__ import annotations

import os
from dataclasses import dataclass


class LimitError(ValueError):
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Limits:
    max_file_bytes: int = _env_int("DOCWEAVE_MAX_FILE_MB", 100) * 1024 * 1024
    # The built-in editor submits its document model as JSON, with images inline as data URIs.
    # Smaller than the file limit on purpose: this body is parsed into Python objects in memory
    # before any handler sees it, so it costs several times its own size.
    max_editor_body_bytes: int = _env_int("DOCWEAVE_MAX_EDITOR_MB", 24) * 1024 * 1024
    max_unzipped_bytes: int = _env_int("DOCWEAVE_MAX_UNZIPPED_MB", 600) * 1024 * 1024
    max_zip_entries: int = _env_int("DOCWEAVE_MAX_ZIP_ENTRIES", 20000)
    max_cells: int = _env_int("DOCWEAVE_MAX_CELLS", 2_000_000)
    max_pages: int = _env_int("DOCWEAVE_MAX_PAGES", 2000)
    max_image_pixels: int = _env_int("DOCWEAVE_MAX_IMAGE_MPIX", 80) * 1_000_000
    max_records_per_job: int = _env_int("DOCWEAVE_MAX_RECORDS_PER_JOB", 20000)
    converter_timeout_s: int = _env_int("DOCWEAVE_CONVERTER_TIMEOUT", 180)
    ocr_timeout_s: int = _env_int("DOCWEAVE_OCR_TIMEOUT", 120)


LIMITS = Limits()
