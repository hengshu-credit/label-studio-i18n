
################################ Overview

# This Dockerfile builds a Label Studio environment.
# Build context is the repository root.
# Stages:
# 1. "uv" - Pinned uv binary.
# 2. "frontend-builder" - Compiles the frontend assets using Bun.
# 3. "venv-builder" - Prepares the virtualenv environment.
# 4. "production" - Final image with Label Studio, Nginx, and other dependencies.

# label-studio-sdk is a git-pinned dependency in pyproject.toml/uv.lock
# (maintained by the monorepo sync); uv fetches it during the build.

################################ Stage: uv (pinned uv binary)

################################ Stage: frontend-builder (build frontend assets)
FROM --platform=${BUILDPLATFORM} oven/bun:1.3.11-alpine@sha256:7ed9f74c326d1c260abe247ac423ccbf5ac92af62bb442d515d1f92f21e8ea9b AS frontend-deps
ENV BUILD_NO_SERVER=true \
    BUILD_NO_HASH=true \
    BUILD_NO_CHUNKS=true \
    BUILD_MODULE=true \
    NODE_ENV=production \
    NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /label-studio/web

RUN apk add --no-cache git

COPY web/package.json \
     web/bun.lock \
     ./
ARG UPDATE_LOCK=false
RUN --mount=type=cache,target=/root/.bun/install/cache,id=bun-install-cache-lso,sharing=locked \
    if [ "$UPDATE_LOCK" = "true" ]; then bun install; else bun install --frozen-lockfile --prefer-offline; fi

FROM frontend-deps AS frontend-builder
COPY web ./
# Target path for django-manifest-plugin → label_studio/core/static/js/manifest.json (collectstatic input).
RUN mkdir -p /label-studio/label_studio/core/static/js
COPY pyproject.toml ../pyproject.toml
RUN NODE_ENV=test bun test libs/app-common/src/i18n libs/app-common/src/pages/AccountSettings/sections/Language
RUN --mount=type=cache,target=/root/.bun/install/cache,id=bun-install-cache-lso,sharing=locked \
    bun run build

################################ Stage: venv-builder (prepare the virtualenv)
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS venv-builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR="/.uv-cache" \
    UV_PROJECT_ENVIRONMENT="/label-studio/.venv" \
    UV_LINK_MODE="copy" \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/label-studio/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git libpcre2-dev && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv==0.13.5

WORKDIR /label-studio

# --- Phase 1: Install dependencies (highly cacheable) ---
# Copy only manifest files — no source code. This layer is invalidated only
# when pyproject.toml or uv.lock change, preserving expensive C-extension
# builds across source edits. label-studio-sdk comes from its git pin.
COPY pyproject.toml uv.lock README.md ./

ARG INCLUDE_DEV=false

RUN --mount=type=cache,target=/.uv-cache,id=uv-cache-alpine,sharing=locked \
    if [ "$INCLUDE_DEV" = "true" ]; then \
        uv sync --frozen --no-install-project --group test --extra uwsgi; \
    else \
        uv sync --frozen --no-install-project --no-dev --extra uwsgi; \
    fi

# --- Phase 2: Install project (only reruns on source changes) ---
FROM venv-builder AS backend-source
COPY label_studio ./label_studio

# Vite emits this path in frontend-builder; collectstatic must see it before STATIC_ROOT is populated.
COPY --from=frontend-builder /label-studio/label_studio/core/static/js/manifest.json ./label_studio/core/static/js/manifest.json
COPY --from=frontend-builder /label-studio/label_studio/core/static/js/sw.js ./label_studio/core/static/js/sw.js
COPY --from=frontend-builder /label-studio/label_studio/core/static/js/sw.js.map ./label_studio/core/static/js/sw.js.map

RUN --mount=type=cache,target=/.uv-cache,id=uv-cache-alpine,sharing=locked \
    if [ "$INCLUDE_DEV" = "true" ]; then \
        uv sync --frozen --group test --extra uwsgi; \
    else \
        uv sync --frozen --no-dev --extra uwsgi; \
    fi

# Collect static files
RUN DJANGO_SETTINGS_MODULE=core.settings.label_studio python3 ./label_studio/manage.py collectstatic --no-input

FROM scratch AS lockfile
COPY --from=frontend-deps /label-studio/web/bun.lock /bun.lock

################################### Stage: prod
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS production

ENV LS_DIR=/label-studio \
    HOME=/label-studio \
    LABEL_STUDIO_BASE_DATA_DIR=/label-studio/data \
    OPT_DIR=/opt/heartex/instance-data/etc \
    PATH="/label-studio/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=core.settings.label_studio \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR $LS_DIR

# install prerequisites for app
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libglib2.0-0 libgl1 libgomp1 nginx curl bash procps && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p $LS_DIR $LABEL_STUDIO_BASE_DATA_DIR $OPT_DIR && \
    chown -R 1001:0 $LS_DIR $LABEL_STUDIO_BASE_DATA_DIR $OPT_DIR /var/log/nginx /etc/nginx

COPY --chown=1001:0 deploy/default.conf /etc/nginx/nginx.conf

COPY --chown=1001:0 LICENSE LICENSE
COPY --chown=1001:0 licenses licenses
COPY --chown=1001:0 deploy deploy

# Copy files from build stages
COPY --chown=1001:0 --from=backend-source             /label-studio                                  $LS_DIR
COPY --chown=1001:0 --from=frontend-builder           /label-studio/web/dist                        $LS_DIR/web/dist

ARG COMMIT_VERSION=N/A
ARG COMMIT_BRANCH=N/A
ARG COMMIT_MESSAGE=N/A
ARG COMMIT_DATE=N/A
ARG COMMIT_SHA=N/A

ENV COMMIT_VERSION=${COMMIT_VERSION} \
    COMMIT_BRANCH=${COMMIT_BRANCH} \
    COMMIT_MESSAGE=${COMMIT_MESSAGE} \
    COMMIT_DATE=${COMMIT_DATE} \
    COMMIT_SHA=${COMMIT_SHA}
RUN echo "info = {'message': '${COMMIT_MESSAGE}', 'commit': '${COMMIT_SHA}', 'date': '${COMMIT_DATE}', 'branch': '${COMMIT_BRANCH}', 'version': '${COMMIT_VERSION}'}" > $LS_DIR/label_studio/core/version_.py

USER 1001

EXPOSE 8080

ENTRYPOINT ["./deploy/docker-entrypoint.sh"]
CMD ["label-studio"]
