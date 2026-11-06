# Imagen de la API de lectura (T107, ADR 0044). Contexto de build: raiz del repo.
#   docker build -f backend/Dockerfile .
# Solo instala dependencias del servicio (sin grupos por defecto, solo `serve`).

FROM python:3.12.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=0 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-default-groups --group serve --no-install-project
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-default-groups --group serve

FROM python:3.12.12-slim-bookworm AS runtime
RUN apt-get update \
    && apt-get install --no-install-recommends -y tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin app
WORKDIR /app/backend
COPY --from=builder /app/backend/.venv ./.venv
COPY --from=builder /app/backend/src ./src
COPY --from=builder /app/backend/alembic ./alembic
COPY --from=builder /app/backend/alembic.ini ./alembic.ini
COPY config/pipeline.toml /app/config/pipeline.toml
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "nocturna.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
