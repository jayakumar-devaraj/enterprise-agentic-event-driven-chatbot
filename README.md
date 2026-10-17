# enterprise-agentic-event-driven-chatbot

## Tech stack

Versions read from `pyproject.toml` / `uv.lock`. Only what is installed today is listed; each task adds
its dependencies here in the same commit.

- **Language / tooling:** Python 3.13.13 (`.python-version`), uv workspace (`packages/common`,
  `services/{gateway,worker,seed}`), build backend `uv_build` 0.13.x.
- **Testing:** pytest 9.1.1 (unit by default; `-m integration` for real-service tests),
  pytest-asyncio 1.4.0 (`asyncio_mode=auto`), pytest-cov 7.1.0 (coverage reports).
- **Lint:** ruff 0.17.1 (`ruff check`, `ruff format --check`).
- **CI:** GitHub Actions (`actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.3.0) running lint and
  unit tests on every push and pull request.
