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

FROM python:3.12-slim-bookworm
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
RUN pip install --no-cache-dir -r apps/docs/requirements.txt -r services/identity/requirements.txt
# AGPL-3.0 §13：/api/source-code 提供的是**正在运行的程序**的对应源代码。重组之后"正在运行的
# 程序"不只是 DocWeave 自己的目录——进程是由共享包、账户服务和角色组装拼起来的，所以它们都要
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
RUN pip install --no-cache-dir --no-deps \
      -e packages/core -e packages/auth-client/python \
      -e services/identity -e apps/portal -e apps/docs -e infra/server
# LICENSE is deliberately absent: this project has not chosen one yet, and COPY of a missing
# file fails the build. The source archive reports it as missing rather than pretending.
RUN useradd -m synwevo && mkdir -p /data && chown synwevo /data
USER synwevo
VOLUME ["/data"]
EXPOSE 8765
CMD ["python", "-m", "synwevo_server.run"]
