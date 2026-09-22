#!/usr/bin/env bash
# One-time setup on macOS / Linux: Python venv, web UI build, OCR language data.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
$PY -m venv backend/.venv
backend/.venv/bin/pip install -U pip
backend/.venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
mkdir -p backend/tessdata
for l in chi_sim eng osd; do
  [ -f backend/tessdata/$l.traineddata ] || curl -fsSL -o backend/tessdata/$l.traineddata \
    https://github.com/tesseract-ocr/tessdata_fast/raw/main/$l.traineddata
done
echo "OK. Optional system tools: LibreOffice (legacy formats + PDF export) and Tesseract (OCR) – see docs/DEPLOY.md"
