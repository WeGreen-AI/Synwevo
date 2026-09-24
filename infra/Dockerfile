# Synwevo 自托管镜像：单进程部署（SYNWEVO_ROLE=all）—— Synwevo Docs 工作台 + Synwevo Account，同一个源站。
# 构建上下文是**仓库根目录**：
#     docker build -f infra/Dockerfile -t synwevo .
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
# npm workspaces：锁文件在仓库根目录，各工作区的 package.json 要一起复制进来才能 `npm ci`。
COPY package.json package-lock.json ./
COPY packages/ui/package.json packages/ui/
COPY packages/auth-client/web/package.json packages/auth-client/web/
COPY apps/docs/web/package.json apps/docs/web/
COPY apps/portal/web/package.json apps/portal/web/
COPY services/identity/web/package.json services/identity/web/
RUN npm ci
COPY packages/ packages/
COPY apps/docs/web/ apps/docs/web/
COPY apps/portal/web/ apps/portal/web/
COPY services/identity/web/ services/identity/web/
# 单进程部署提供两个外壳：工作台与账户页。它们的静态资源目录不同名（assets / account-assets），
# 否则同名的 /assets 会互相遮盖——这正是"页面能开但样式没了"的来源。
RUN npm run build -w @synwevo/docs-web && npm run build -w @synwevo/account-web && npm run build -w @synwevo/workspace-web

# Trixie's LibreOffice 25 supports the XLOOKUP formulas in the real engine/reference suite.
# Bookworm's 7.4 returns #NAME? for those workbooks.
FROM python:3.12-slim-trixie
ENV PYTHONUNBUFFERED=1 SYNWEVO_IN_CONTAINER=1 SYNWEVO_HOST=0.0.0.0 SYNWEVO_PORT=8765 SYNWEVO_DATA_DIR=/data \
    SYNWEVO_ROLE=all \
    SYNWEVO_DOCS_UI_DIR=/app/apps/docs/web/dist \
    SYNWEVO_ACCOUNTS_UI_DIR=/app/services/identity/web/dist \
    SYNWEVO_PORTAL_UI_DIR=/app/apps/portal/web/dist \
    SYNWEVO_DOCS_TESSDATA=/usr/share/tesseract-ocr/5/tessdata
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
      fonts-noto-cjk fontconfig \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# 依赖先装，源码后复制：改一行代码不该让 pip 重跑一遍。
COPY packages/core/requirements.txt packages/core/requirements.txt
COPY packages/auth-client/python/requirements.txt packages/auth-client/python/requirements.txt
COPY services/identity/requirements.txt services/identity/requirements.txt
COPY apps/portal/requirements.txt apps/portal/requirements.txt
COPY apps/docs/requirements.txt apps/docs/requirements.txt
COPY infra/server/requirements.txt infra/server/requirements.txt
COPY infra/server/requirements.lock infra/server/requirements-build.txt infra/server/
# 部署组装的 requirements 就是三个模块的并集，版本全部来自各模块固定的文件。
RUN pip install --no-cache-dir -r infra/server/requirements-build.txt \
    && pip install --no-cache-dir -r infra/server/requirements.txt
# AGPL-3.0 §13：/api/source-code 提供的是**正在运行的程序**的对应源代码。重组之后"正在运行的
# 程序"不只是 Synwevo Docs 自己的目录——进程是由共享包、账户服务和角色组装拼起来的，所以它们都要
# 在镜像里，否则那个压缩包会在声称完整的同时少掉一半。
COPY packages/ packages/
COPY services/identity/ services/identity/
COPY apps/portal/ apps/portal/
COPY apps/docs/ apps/docs/
COPY infra/server/ infra/server/
COPY scripts/ scripts/
COPY tests/ tests/
COPY docs/ docs/
COPY README.md NOTICE pyproject.toml package.json package-lock.json requirements-dev.txt ./
COPY infra/Dockerfile infra/docker-compose.yml infra/
COPY --from=ui /ui/apps/docs/web/dist apps/docs/web/dist
COPY --from=ui /ui/services/identity/web/dist services/identity/web/dist
COPY --from=ui /ui/apps/portal/web/dist apps/portal/web/dist
# `pip check`: every module's declared dependencies must be satisfied by the pinned set, or the
# image fails here instead of at the first request that reaches an undeclared import.
RUN pip install --no-cache-dir --no-deps --no-build-isolation \
      -e packages/core -e packages/auth-client/python \
      -e services/identity -e apps/portal -e apps/docs -e infra/server \
    && pip check
# LICENSE is deliberately absent: this project has not chosen one yet, and COPY of a missing
# file fails the build. The source archive reports it as missing rather than pretending.
# The data directory is the volume, owned by the unprivileged user the server runs as. HOST and
# DATA_DIR above describe the container itself; infra/docker-compose.yml pins them again so an
# operator's .env cannot move the database out of the volume or bind the container's loopback.
# The SSH receiver extracts source with umask 077. COPY preserves directory modes, so
# root-owned 0700 source directories would be unreadable to the runtime user. Only the
# shipped application source is made readable; /data and host configuration stay private.
RUN chmod -R a+rX /app \
    && useradd -m synwevo && mkdir -p /data && chown synwevo /data && chmod 700 /data
USER synwevo
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/readyz', timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "synwevo_server.run"]
