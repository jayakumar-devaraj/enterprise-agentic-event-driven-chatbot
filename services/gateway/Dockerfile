# Multi-stage, slim, non-root. Build context is the repo root (uv workspace).
FROM python:3.13.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv sync --locked --no-dev --no-editable --package gateway

FROM python:3.13.13-slim
RUN useradd --system --uid 10001 --create-home app
COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1 
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
