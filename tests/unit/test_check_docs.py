"""The docs checker must flag dangling links and ignore examples, directories and external URLs."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py"
spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)


def _tree(root: Path, files: dict[str, str]) -> set[str]:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return set(files)


def test_flags_only_links_that_do_not_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    tracked = _tree(
        tmp_path,
        {
            "docs/a.md": (
                "[ok](b.md) [up](../README.md#top) [dir](sub) [web](https://example.com/x.md)\n"
                "[gone](missing.md) [gone2](../nope/x.md)\n"
                "```md\n[example](not-checked.md)\n```\n"
            ),
            "docs/b.md": "text",
            "docs/sub/c.md": "text",
            "README.md": "text",
        },
    )
    problems = check_docs.broken_links(tracked, ["docs/a.md"])
    assert problems == [
        "docs/a.md: broken link -> missing.md",
        "docs/a.md: broken link -> ../nope/x.md",
    ]


def test_manifest_lines_must_be_complete_and_name_real_prs_and_commits(monkeypatch):
    monkeypatch.setattr(check_docs, "pr_is_merged", lambda n: n == "134")
    head = check_docs.git("rev-parse", "HEAD").strip()
    good = {
        "pr": 134,
        "merge_commit": head,
        "session": "s",
        "transcript_sha256": "0" * 64,
        "transcript_bytes": 1,
        "recorded": "2026-11-13T00:00:00Z",
    }
    lines = [
        json.dumps(good),
        "not json",
        json.dumps({"pr": 134}),
        json.dumps({**good, "pr": 999, "merge_commit": "0" * 40}),
    ]
    problems = check_docs.manifest_problems("\n".join(lines))
    assert problems == [
        f"{check_docs.MANIFEST}:2: not JSON",
        f"{check_docs.MANIFEST}:3: missing merge_commit, session, transcript_sha256, "
        "transcript_bytes, recorded",
        f"{check_docs.MANIFEST}:4: unknown merge commit {'0' * 40}",
        f"{check_docs.MANIFEST}:4: PR #999 is not a merged pull request",
    ]
