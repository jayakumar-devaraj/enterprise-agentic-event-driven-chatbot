# Prompt: Python Demo Script, Built in Phases

One Python script that drives the local stack and shows the demo, launched the same way from
CMD, PowerShell, or a Mac terminal:

    uv run python scripts/end_to_end_demo.py

Built in three phases. Each phase is a separate prompt you paste only when you have time for
it, and each one extends the previous script instead of rewriting it.

| Phase | Goal | Agent time |
|---|---|---|
| 1 - Minimum demo | Takes input, calls the API, shows the result | ~10-15 min |
| 2 - Robust on your machine | Starts dependencies itself, clear errors, safe to re-run | ~20-30 min |
| 3 - Cross-platform | Verified on both Mac and Windows, background server handling | ~30+ min |

Fill in the bracketed placeholders in Phase 1. Later phases reuse the same values.

---

## Phase 1 - Minimum demo

```
Write one short Python script, [SCRIPT_PATH - e.g. "scripts/end_to_end_demo.py"], that shows
this demo: [DESCRIBE THE DEMO - e.g. "create a tenant, ingest a sample file, ask it a
question via POST /chat, print the JSON response"].

Assume the stack is already running (I start it myself with `docker compose up -d`). API base
URL: [e.g. "http://localhost:8000"].

Keep it small - about 60 lines, no over-engineering. Requirements:
1. Launched with `uv run python [SCRIPT_PATH]` from the project root. Build file paths from
   `Path(__file__).resolve().parents[N]` (N = folders below the project root) so paths don't
   depend on the current directory.
2. Input: [WHAT THE USER TYPES - e.g. "the question to ask"]. Accept it as an argparse
   argument. If it's missing, prompt with `input()`, with a sensible default if the user
   just presses Enter.
3. Call the API steps in order and print each step's name and its response as readable JSON
   (`json.dumps(..., indent=2, ensure_ascii=False)`).
4. If the API can't be reached, print one line - "API not reachable at <url> - is
   `docker compose up -d` running?" - and exit with code 1. If a step returns an HTTP error,
   print the step, status code, and response body, then exit 1.
5. Call `sys.stdout.reconfigure(encoding="utf-8")` at startup so non-ASCII text prints
   correctly on Windows.
6. Use only the standard library (`urllib.request`, `json`, `argparse`) or HTTP packages the
   project already depends on. Don't add dependencies. No `shell=True`.

Then run it against the live stack once with the argument and once with the prompt, and show
me the actual output. Tell me only what you actually ran and saw.
```

---

## Phase 2 - Robust on your machine

```
The script at [SCRIPT_PATH] already works as a minimal demo. Read it first. Extend it - don't
rewrite it - and keep its current behavior (arguments, prompt, output format) working.

Dependencies it should now manage itself: [LIST - e.g. "Postgres via `docker compose up -d`",
"the API via `uv run uvicorn src.main:app --port 8000`"].

1. Dependencies: for each one, check whether it's already up; if not, start it; then poll its
   real readiness signal (health endpoint, `docker compose ps` reporting healthy) with a
   timeout. Confirm the service is ours (e.g. an expected field in the health response), not
   just that something answers on the port. Leave started services running afterward.
2. Docker: before using it, find it with `shutil.which` and run `docker info`. If it fails,
   print "Docker is installed but not running - start Docker Desktop" and exit 1.
3. Clear failures: wrap main() so every failure prints one readable line (what failed, what
   to check) and exits 1. Show a traceback only with `--debug`. Check every subprocess's
   return code. Put a timeout on every network call and subprocess. On Ctrl+C, say what's
   still running and exit 130.
4. Encoding: pass `encoding="utf-8"` to every `open()` and every subprocess whose output you
   read. Give child Python processes `PYTHONUTF8=1` via the env you pass them.
5. Re-run safety: if the demo creates anything with a unique name, suffix it with part of a
   uuid4 so running twice in a row works. Include one non-ASCII character in the demo data.
6. Input: only prompt when `sys.stdin.isatty()`; otherwise use the default. Catch
   `EOFError`/`KeyboardInterrupt` around `input()` and fall back to the default.

Verify on this machine, against the live stack:
- dependencies already running, and from cold. To get cold, stop only what the script
  itself would start. Never delete volumes or data (no `docker compose down -v`), and
  restore whatever was running before you began.
- one failure (e.g. Docker stopped): clear message and exit 1, not a hang or traceback.
- run twice in a row: no duplicate-name error.
- stdin not a terminal (`< /dev/null` on Mac, `< NUL` in CMD): uses the default, no hang.
- non-ASCII text in the output prints correctly.

If two attempts to fix the same problem fail, stop and tell me what you tried. Report only
what you actually ran and saw, and what's left running with how to stop it.
```

---

## Phase 3 - Cross-platform (Mac + Windows)

```
The script at [SCRIPT_PATH] works and is robust on one machine. Read it first. Extend it -
don't rewrite it - and keep everything from the earlier phases working.

OSes it must support: macOS and Windows. OSes you can actually run in this session: [e.g.
"Windows only" - or "unknown: determine it and tell me first"].

1. No shell dependence: every subprocess is an argument list (no `shell=True`, no
   `os.system`), and every executable is found with `shutil.which` (Windows tools are often
   `.cmd` shims). The user must never need to set environment variables or run shell-specific
   setup before `uv run python [SCRIPT_PATH]`.
2. Background server: if the script starts a long-running server that should outlive it, use
   `subprocess.Popen` with output going to a log file under the project root (e.g.
   `.demo/api.log`). Detach it with `start_new_session=True` on macOS and
   `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS` on
   Windows. Write its PID to a file. Print the log path and the stop command for the current
   OS (`kill <pid>` / `taskkill /PID <pid> /T /F`).
3. Paths: everything via pathlib from the project root. No hardcoded `/` or `\`.

Verify on every OS in scope that you can actually run:
- launched from CMD and from PowerShell on Windows, and from Terminal on macOS: same command,
  same result
- from a different directory: `uv run --project <root> python <root>/[SCRIPT_PATH]`
- the background server survives the script exiting, and the printed stop command works
- re-run all the Phase 2 checks on this OS

Report a matrix: one row per check, one column per OS, each cell VERIFIED / FAILED (what you
saw) / NOT RUN (why). An OS you couldn't run is NOT RUN, never assumed from the other one.
Also report what's still running and how to stop it on each OS, and the files changed.
```
