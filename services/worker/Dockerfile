# Multi-stage, slim, non-root. Build context is the repo root (uv workspace).
FROM python:3.13.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv sync --locked --no-dev --no-editable --package worker
# Model downloads happen here, at build time only; the running container is offline.
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"
FROM python:3.13.13-slim
RUN useradd --system --uid 10001 --create-home app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /models /models
ENV PATH=/app/.venv/bin:$PATH HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/models
USER app
CMD ["python", "-m", "worker.main"]
