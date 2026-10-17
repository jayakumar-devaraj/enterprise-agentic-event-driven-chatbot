# Task 1 — Workspace skeleton

**Purpose:** uv workspace with `packages/common`, `services/{gateway,worker,seed}` importable; README
Tech Stack; `.gitattributes` (LF); `SPEC.md` = brief "Contracts" plus approved additions; complete
`.env.example`; GitHub Actions CI (ruff + unit tests); CLAUDE.md §9 filled (approved exception).
**Inputs:** locked stack, config table in `docs/master_plan.md`. **Outputs:** the files above, `uv.lock`.
**Verify:** `uv sync` succeeds; `uv run pytest tests/unit` runs an import smoke test that passes;
`uv run ruff check .` clean; CI run green on the PR.
