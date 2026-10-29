"""The docs checker must flag dangling links and ignore examples, directories and external URLs."""

import importlib.util
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
