"""Provenance manifest (ADR 0017): the creating call as evidence, and prefix-hash verification."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "provenance.py"
spec = importlib.util.spec_from_file_location("provenance", SCRIPT)
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)

URL = "https://github.com/o/r/pull/7"


def _transcript(path: Path, command: str, output: str) -> Path:
    use = {"type": "tool_use", "id": "t1", "input": {"command": command}}
    result = {"type": "tool_result", "tool_use_id": "t1", "content": output}
    lines = [{"message": {"content": [use]}}, {"message": {"content": [result]}}]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def test_only_a_create_call_that_returned_the_url_counts(tmp_path):
    assert provenance.created_pr(
        _transcript(tmp_path / "a.jsonl", "gh pr create --base main", URL), URL
    )
    assert not provenance.created_pr(_transcript(tmp_path / "b.jsonl", "gh pr view 7", URL), URL)
    assert not provenance.created_pr(
        _transcript(tmp_path / "c.jsonl", "gh pr create", URL.replace("/7", "/8")), URL
    )


def test_a_transcript_that_grew_still_verifies_and_an_edited_one_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "MANIFEST", tmp_path / "manifest.jsonl")
    transcript = tmp_path / "sess-1.jsonl"
    transcript.write_bytes(b"line one\n")
    entry = {
        "pr": 7,
        "session": "sess-1",
        "transcript_sha256": provenance.digest(b"line one\n"),
        "transcript_bytes": 9,
    }
    provenance.MANIFEST.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    transcript.write_bytes(b"line one\nline two\n")
    assert provenance.verify(transcript) == []
    transcript.write_bytes(b"line 1!!\nline two\n")
    assert provenance.verify(transcript) == ["PR #7: transcript does not match its recorded hash"]


def test_add_refuses_an_unmerged_pr(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "MANIFEST", tmp_path / "manifest.jsonl")
    monkeypatch.setattr(provenance, "gh_pr", lambda n: {"state": "OPEN", "url": URL})
    with pytest.raises(SystemExit, match="not merged"):
        provenance.add(7, _transcript(tmp_path / "s.jsonl", "gh pr create", URL))
    assert not provenance.MANIFEST.exists()
