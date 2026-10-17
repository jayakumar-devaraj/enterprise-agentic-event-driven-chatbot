# Prompt: Repo Structure Creation

Use this prompt to have an AI coding agent (re)create this repository's exact
directory and file layout from scratch — e.g. to bootstrap a new project with
the same skeleton. Paste it verbatim and fill in `<TARGET_DIR>`.

---

## Prompt

```
Create the following directory and file structure exactly as listed below,
rooted at: <TARGET_DIR>

Rules:
- Create every directory and file listed. Do not create, rename, or omit
  anything not listed.
- Create all files empty (0 bytes) — do not add placeholder text, comments,
  or boilerplate content to any file.
- If <TARGET_DIR> does not exist, create it first.
- If any file or directory in this list already exists, leave it untouched
  (do not overwrite or truncate existing files).
- Preserve the exact names, casing, and extensions shown below.
- Two directories in this structure (.claude/agents/ and .claude/skills/)
  are intentionally empty — create the directories themselves (e.g. with a
  .gitkeep file if your tooling cannot commit empty directories to git, but
  otherwise leave them empty).
- After creating everything, list the resulting structure back to me so I
  can confirm it matches.

Structure to create:

<TARGET_DIR>/
├── .claude/
│   ├── agents/
│   └── skills/
├── .github/
│   └── workflows/
│       └── ci.yml
├── config/
│   ├── agents_config.yaml
│   ├── logging_config.json
│   └── tools_config.yaml
├── docs/
│   ├── adr/
│   │   └── 0001-adr-name.md
│   ├── architecture/
│   │   └── ARCHITECTURE.md
│   ├── prompts/
│   │   ├── hackathon.md
│   │   ├── master_prompt.md
│   │   ├── repo_audit.md
│   │   └── repo_setup.md
│   ├── specs/
│   │   └── 0001-spec-name.md
│   └── master_plan.md
├── scripts/
│   ├── end_to_end_demo.ps1
│   └── end_to_end_demo.sh
├── src/
│   ├── agents/
│   │   └── __init__.py
│   ├── orchestration/
│   │   ├── __init__.py
│   │   ├── graph.py
│   │   └── state.py
│   ├── routers/
│   │   └── __init__.py
│   ├── tools/
│   │   └── __init__.py
│   ├── utils/
│   │   └── __init__.py
│   └── main.py
├── tests/
│   ├── integration/
│   │   └── test_orchestration.py
│   └── unit/
│       ├── test_agents.py
│       ├── test_graph.py
│       ├── test_state.py
│       └── test_tools.py
├── .env
├── .env.example
├── .gitignore
├── .python-version
├── AGENTS.md
├── CLAUDE.md
├── DockerFile
├── README.md
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
└── uv.lock
```

---

## Notes

- Replace `<TARGET_DIR>` with an absolute or relative path before running
  (e.g `./my-new-project`).
- This prompt only scaffolds empty files/directories — it does not populate
  any file with content. Run a separate prompt/task afterward to fill in
  code, config, or docs.
- `.env` is included here only because it appeared in this repo's snapshot;
  do not commit real secrets into it. Consider excluding `.env` from the
  structure if scaffolding a shareable/public template.
