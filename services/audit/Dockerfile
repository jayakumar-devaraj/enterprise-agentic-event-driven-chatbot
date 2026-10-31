# Multi-stage, slim, non-root. Build context is the repo root (uv workspace).
FROM python:3.13.18-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv sync --locked --no-dev --no-editable --package audit
FROM python:3.13.18-slim
# pip is unused at runtime (the app runs from /app/.venv) and its vendored packages carry CVEs.
RUN useradd --system --uid 10001 --create-home app && python -m pip uninstall -y -q pip
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER app
CMD ["python", "-m", "audit.main"]
