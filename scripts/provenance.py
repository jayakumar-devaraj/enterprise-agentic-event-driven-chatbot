"""Provenance manifest (ADR 0017): which agent session created each merged pull request.

`docs/provenance/manifest.jsonl` holds one line per merged PR: the PR number, its merge commit, the
Claude Code session ID, and the SHA-256 and byte length of that session's transcript when the line
was written. Transcripts stay on the author's machine. A transcript only grows by appending, so its
first `transcript_bytes` bytes keep the recorded hash, and anyone given the file can check it.

    uv run python scripts/provenance.py add --pr 134 --transcript <path/to/session.jsonl>
    uv run python scripts/provenance.py verify --transcript <path/to/session.jsonl>

`add` refuses unless the PR is merged and the transcript contains the `gh pr create` call whose
output is that PR's URL: a session is recorded only from that evidence, never inferred.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "provenance" / "manifest.jsonl"
FIELDS = ("pr", "merge_commit", "session", "transcript_sha256", "transcript_bytes", "recorded")


def created_pr(transcript: Path, url: str) -> bool:
    """True if a `gh pr create` tool call in the transcript returned `url`."""
    calls: dict[str, str] = {}
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            content = (json.loads(line).get("message") or {}).get("content")
        except ValueError:
            continue
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                calls[part.get("id", "")] = json.dumps(part.get("input", {}))
            elif part.get("type") == "tool_result":
                if "gh pr create" in calls.get(part.get("tool_use_id", ""), "") and url in (
                    json.dumps(part.get("content", ""))
                ):
                    return True
    return False


def digest(data: bytes) -> str:
    """SHA-256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def entries() -> list[dict]:
    """The manifest's lines, oldest first."""
    if not MANIFEST.exists():
        return []
    return [json.loads(line) for line in MANIFEST.read_text("utf-8").splitlines() if line]


def gh_pr(number: int) -> dict:
    """State, URL and merge commit of a PR."""
    out = subprocess.run(
        ["gh", "pr", "view", str(number), "--json", "state,url,mergeCommit"],
        cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout  # fmt: skip
    return json.loads(out)


def add(number: int, transcript: Path) -> dict:
    """Append the line for a merged PR created in `transcript`."""
    if any(e["pr"] == number for e in entries()):
        raise SystemExit(f"PR #{number} is already in the manifest")
    pr = gh_pr(number)
    if pr["state"] != "MERGED":
        raise SystemExit(f"PR #{number} is {pr['state']}, not merged")
    if not created_pr(transcript, pr["url"]):
        raise SystemExit(f"{transcript.name} has no `gh pr create` call that returned {pr['url']}")
    data = transcript.read_bytes()
    entry = {
        "pr": number,
        "merge_commit": pr["mergeCommit"]["oid"],
        "session": transcript.stem,
        "transcript_sha256": digest(data),
        "transcript_bytes": len(data),
        "recorded": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def verify(transcript: Path) -> list[str]:
    """Check every line recorded for this transcript's session; return the mismatches."""
    data, problems = transcript.read_bytes(), []
    mine = [e for e in entries() if e["session"] == transcript.stem]
    if not mine:
        problems.append(f"no manifest line names session {transcript.stem}")
    for e in mine:
        if digest(data[: e["transcript_bytes"]]) != e["transcript_sha256"]:
            problems.append(f"PR #{e['pr']}: transcript does not match its recorded hash")
    return problems


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--pr", type=int, required=True)
    a.add_argument("--transcript", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--transcript", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "add":
        print(json.dumps(add(args.pr, args.transcript)))
    else:
        found = verify(args.transcript)
        print("\n".join(found) or "every recorded hash matches")
        sys.exit(1 if found else 0)
