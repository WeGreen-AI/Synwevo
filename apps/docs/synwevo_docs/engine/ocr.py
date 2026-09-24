"""Local OCR with the Tesseract CLI (Apache-2.0).  Runs as a subprocess with a
time limit; nothing is sent to external services.

The recognised words (text, box, confidence) are stored separately from the
original image/page – the visual preview always shows the original pixels.
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from .limits import LIMITS

_TESSDATA_DEFAULT = Path(__file__).resolve().parents[2] / "tessdata"


def tesseract_path() -> str | None:
    return os.environ.get("DOCWEAVE_TESSERACT") or shutil.which("tesseract")


def tessdata_dir() -> str | None:
    env = os.environ.get("DOCWEAVE_TESSDATA")
    if env and Path(env).exists():
        return env
    if (_TESSDATA_DEFAULT / "chi_sim.traineddata").exists():
        return str(_TESSDATA_DEFAULT)
    return None


def available_langs() -> list[str]:
    exe = tesseract_path()
    if not exe:
        return []
    cmd = [exe, "--list-langs"]
    td = tessdata_dir()
    if td:
        cmd += ["--tessdata-dir", td]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return [l.strip() for l in out.stdout.splitlines()[1:] if l.strip()]
    except Exception:
        return []


class OcrError(RuntimeError):
    pass


def ocr_png(png: bytes, langs: str = "chi_sim+eng", psm: int = 3) -> dict:
    """OCR a PNG image.  Returns {'w', 'h', 'words': [{'t', 'r' (normalised), 'conf', 'line'}], 'engine'}"""
    exe = tesseract_path()
    if not exe:
        raise OcrError("未安装 Tesseract OCR，无法识别扫描件/图片（安装说明见 docs/DEPLOY.md）")
    have = available_langs()
    use = [l for l in langs.split("+") if l in have] or (["eng"] if "eng" in have else [])
    if not use:
        raise OcrError("Tesseract 缺少识别语言数据（chi_sim / eng）")
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    w, h = im.size
    with tempfile.TemporaryDirectory(prefix="dwocr-") as tmp:
        src = Path(tmp) / "page.png"
        src.write_bytes(png)
        cmd = [exe]
        td = tessdata_dir()
        if td:
            cmd += ["--tessdata-dir", td]
        # TSV via a variable, not the "tsv" config file: that file lives in the system tessdata
        # directory, which is not the one used when our bundled models are selected
        cmd += [str(src), "stdout", "-l", "+".join(use), "--psm", str(psm), "-c", "tessedit_create_tsv=1"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=LIMITS.ocr_timeout_s)
        except subprocess.TimeoutExpired as e:
            raise OcrError(f"OCR 超时（{LIMITS.ocr_timeout_s} 秒）") from e
        err = proc.stderr.decode("utf-8", "ignore")
        if proc.returncode != 0:
            raise OcrError("OCR 失败：" + err[-300:])
        if "Failed loading language" in err or "Error opening data file" in err:
            raise OcrError("Tesseract 无法加载识别语言数据：" + err.strip()[-300:])
    words = []
    reader = csv.DictReader(io.StringIO(proc.stdout.decode("utf-8", "ignore")), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        try:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            conf = float(row.get("conf", -1))
            x, y, ww, hh = int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"])
        except (TypeError, ValueError, KeyError):
            continue
        words.append({"t": text, "r": [x / w, y / h, (x + ww) / w, (y + hh) / h], "conf": round(conf, 1),
                      "line": f"{row['block_num']}.{row['par_num']}.{row['line_num']}", "n": int(row["word_num"])})
    return {"w": w, "h": h, "words": words, "engine": f"tesseract ({'+'.join(use)})"}


def _is_cjk(ch: str) -> bool:
    return bool(ch) and unicodedata.east_asian_width(ch) in ("W", "F")


def join_ocr_words(words: list[dict]) -> str:
    lines: dict[str, list[dict]] = {}
    order = []
    for w in words:
        if w["line"] not in lines:
            lines[w["line"]] = []
            order.append(w["line"])
        lines[w["line"]].append(w)
    out = []
    for key in sorted(order, key=lambda k: min(x["r"][1] for x in lines[k])):
        ln = sorted(lines[key], key=lambda x: x["r"][0])
        s = ""
        for w in ln:
            if s and not (_is_cjk(s[-1]) and _is_cjk(w["t"][:1])):
                s += " "
            s += w["t"]
        out.append(s)
    return "\n".join(out)
