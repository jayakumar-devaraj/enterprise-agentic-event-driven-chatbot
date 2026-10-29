"""Fail when a committed document points at something that does not exist.

Checks, over every tracked Markdown file:
- relative links resolve to a tracked file or directory (links inside code fences are examples and
  are skipped);
- every commit hash and `#N` pull request number cited in docs/AI_ORCHESTRATION.md exists: hashes in
  this history, pull requests as merged on GitHub (merge-commit subjects when `gh` is unavailable).

Standard library only; needs the full git history (`fetch-depth: 0` in CI). Exit 1 lists problems.
"""

import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)
HASH = re.compile(r"`([0-9a-f]{7,40})`")
PR = re.compile(r"#(\d{1,4})\b")
ORCHESTRATION = "docs/AI_ORCHESTRATION.md"


def git(*args: str) -> str:
    """Run a git command at the repository root and return stdout."""
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def broken_links(tracked: set[str], md_files: list[str]) -> list[str]:
    """Relative Markdown links whose target is not a tracked file or directory."""
    problems = []
    for name in md_files:
        text = FENCE.sub("", (ROOT / name).read_text(encoding="utf-8"))
        for target in LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue
            path = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if path not in tracked and not any(t.startswith(path + "/") for t in tracked):
                problems.append(f"{name}: broken link -> {target}")
    return problems


def pr_is_merged(number: str) -> bool:
    """Ask GitHub whether the PR merged; without the gh CLI, fall back to merge-commit subjects."""
    if shutil.which("gh"):
        done = subprocess.run(
            ["gh", "pr", "view", number, "--json", "state", "--jq", ".state"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        return done.returncode == 0 and done.stdout.strip() == "MERGED"
    return number in set(re.findall(r"#(\d+)\b", git("log", "--merges", "--format=%s")))


def unknown_references() -> list[str]:
    """Commit hashes and PR numbers in the orchestration log that do not exist."""
    text = (ROOT / ORCHESTRATION).read_text(encoding="utf-8")
    problems = []
    for h in sorted(set(HASH.findall(text))):
        found = subprocess.run(
            ["git", "cat-file", "-e", f"{h}^{{commit}}"], cwd=ROOT, capture_output=True
        )
        if found.returncode != 0:
            problems.append(f"{ORCHESTRATION}: unknown commit `{h}`")
    for n in sorted(set(PR.findall(text)), key=int):
        if not pr_is_merged(n):
            problems.append(f"{ORCHESTRATION}: PR #{n} is not a merged pull request")
    return problems


def main() -> int:
    """Print every problem found and return a process exit code."""
    tracked = set(git("ls-files").splitlines())
    md_files = sorted(f for f in tracked if f.endswith(".md"))
    problems = broken_links(tracked, md_files) + unknown_references()
    for p in problems:
        print(p)
    print(f"{len(md_files)} Markdown files checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
