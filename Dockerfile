# syntax=docker/dockerfile:1.7
FROM node:24-bookworm-slim AS frontend-deps
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

FROM frontend-deps AS frontend-build
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1 \
    API_BASE_URL=http://127.0.0.1:8000
RUN npm run build

FROM python:3.12-slim-bookworm AS python-deps
WORKDIR /build
COPY requirements.txt requirements-lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim-bookworm AS runtime
LABEL org.opencontainers.image.source="https://github.com/BAITC-Hacks/hack-912ce0e6-aitushka" \
      org.opencontainers.image.description="Explainable transaction graph analyst dashboard"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    MONEY_GRAPH_DATA=/app/data \
    MONEY_GRAPH_CONFIG=/app/config.json \
    API_BASE_URL=http://127.0.0.1:8000 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=python-deps /wheels /wheels
COPY requirements.txt requirements-lock.txt /app/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --no-index --find-links=/wheels -r /app/requirements.txt \
    && rm -rf /wheels \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY src ./src
COPY data ./data
COPY config.json ./config.json
COPY deploy ./deploy
COPY --from=frontend-build /build/frontend/.next/standalone ./web
COPY --from=frontend-build /build/frontend/.next/static ./web/.next/static

USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
  CMD ["python", "/app/deploy/healthcheck.py"]
CMD ["python", "/app/deploy/serve.py"]
