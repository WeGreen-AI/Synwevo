"""Processing limits.  Values come from environment variables so a deployment
can tune them; defaults are sized for a single workstation.

Every limit is read through ``synwevo_core.env.docs``: ``SYNWEVO_DOCS_<NAME>`` first, then
``SYNWEVO_<NAME>``, then the pre-rebrand ``DOCWEAVE_<NAME>`` (with a one-time notice).  Reading
``os.environ`` directly used to drop the legacy names silently, so a deployment that had tuned
``DOCWEAVE_MAX_FILE_MB`` went back to the defaults after the upgrade without a word.
"""
from __future__ import annotations

from dataclasses import dataclass

from synwevo_core import env


class LimitError(ValueError):
    pass


def _env_int(suffix: str, default: int) -> int:
    raw = env.docs(suffix)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        print(f"[synwevo] {env.name_for(suffix, scope='docs')}={raw!r} 不是整数，使用默认值 {default}", flush=True)
        return default


@dataclass
class Limits:
    max_file_bytes: int = _env_int("MAX_FILE_MB", 100) * 1024 * 1024
    # The built-in editor submits its document model as JSON, with images inline as data URIs.
    # Smaller than the file limit on purpose: this body is parsed into Python objects in memory
    # before any handler sees it, so it costs several times its own size.
    max_editor_body_bytes: int = _env_int("MAX_EDITOR_MB", 24) * 1024 * 1024
    max_unzipped_bytes: int = _env_int("MAX_UNZIPPED_MB", 600) * 1024 * 1024
    max_zip_entries: int = _env_int("MAX_ZIP_ENTRIES", 20000)
    # One XML part (document.xml, a sheet) is parsed into a DOM several times its size; the
    # total unzipped limit alone lets a single 600 MB part through.
    max_xml_part_bytes: int = _env_int("MAX_XML_PART_MB", 150) * 1024 * 1024
    max_cells: int = _env_int("MAX_CELLS", 2_000_000)
    # Rows are counted separately from cells: empty rows cost memory too, and a row index
    # far below the data (a few bytes in the file) otherwise expands into millions of rows.
    # Default = the most rows a real Excel sheet can have.
    max_rows: int = _env_int("MAX_ROWS", 1_048_576)
    max_pages: int = _env_int("MAX_PAGES", 2000)
    max_image_pixels: int = _env_int("MAX_IMAGE_MPIX", 80) * 1_000_000
    # Page images (previews, OCR) rendered from PDFs: page size × zoom² is attacker-controlled.
    max_render_pixels: int = _env_int("MAX_RENDER_MPIX", 40) * 1_000_000
    # Text read from HTML / TXT / Markdown sources (decoded characters).
    max_text_chars: int = _env_int("MAX_TEXT_MB", 20) * 1024 * 1024
    # A user-written block-marker regular expression runs over every line of a source.
    max_regex_chars: int = _env_int("MAX_REGEX_CHARS", 200)
    max_records_per_job: int = _env_int("MAX_RECORDS_PER_JOB", 20000)
    # Cells one XLSX repeat content block may write into a document (its rows × copies). Measured
    # (2026-09-29, a 20-column block, 2 lines per copy): about 1.5 KB of resident memory per written
    # cell for the whole fill — the XML, the formula engine's model and its cached values —
    # 400,000 cells peaked at 649 MB. 250,000 keeps one fill near 420 MB, well under the ~3 GB the
    # verify container and a production worker have, with room for the rest of the workbook.
    max_block_cells: int = _env_int("MAX_BLOCK_CELLS", 250_000)
    converter_timeout_s: int = _env_int("CONVERTER_TIMEOUT", 180)
    # Address-space cap for the LibreOffice child process (0 = no cap).
    converter_memory_bytes: int = _env_int("CONVERTER_MEMORY_MB", 8192) * 1024 * 1024
    ocr_timeout_s: int = _env_int("OCR_TIMEOUT", 120)


LIMITS = Limits()
