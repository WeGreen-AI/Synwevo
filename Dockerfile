# Self-hosted image: API + web UI + LibreOffice + Tesseract (chi_sim/eng) + CJK fonts.
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 DOCWEAVE_IN_CONTAINER=1 DOCWEAVE_HOST=0.0.0.0 DOCWEAVE_PORT=8765 DOCWEAVE_DATA_DIR=/data \
    DOCWEAVE_UI_DIR=/app/frontend/dist DOCWEAVE_TESSDATA=/usr/share/tesseract-ocr/5/tessdata
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
      fonts-noto-cjk fontconfig \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=ui /ui/dist frontend/dist
# AGPL-3.0 §13: /api/source-code serves the Corresponding Source of the *running* program, built
# from these files. Without them the archive silently shipped the backend alone while claiming to
# hold the frontend, scripts and docs. The frontend source is part of what a network user runs.
COPY frontend/src frontend/src
COPY frontend/index.html frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/
COPY scripts/ scripts/
COPY docs/ docs/
COPY README.md NOTICE Dockerfile docker-compose.yml ./
# LICENSE is deliberately absent: this project has not chosen one yet, and COPY of a missing
# file fails the build. The source archive reports it as missing rather than pretending.
RUN useradd -m docweave && mkdir -p /data && chown docweave /data
USER docweave
VOLUME ["/data"]
EXPOSE 8765
CMD ["python", "backend/run.py"]
