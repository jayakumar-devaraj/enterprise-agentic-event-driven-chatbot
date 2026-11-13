# 0017 · Provenance manifest for agent-built pull requests

**Status:** Accepted 2026-11-13 (owner's decision on item 2.4 of `docs/reports/audit-followups-2026-11-06.md`).

## Context
The 2026-11-03 audit could not verify which agent session produced which change:
- **No commit markers:** commits carry no agent trailers, because CLAUDE.md §5 forbids them.
- **Unverifiable log column:** the session column in `docs/AI_ORCHESTRATION.md` names transcript files that exist only
  on the author's machine, so a reader cannot check it.

The audit capped the provenance points for this reason. Adding markers to past commits would mean rewriting history,
which this repository never does.

## Decision
From now on, each pull request an agent session creates gets one line in `docs/provenance/manifest.jsonl` after it
merges. The line holds:
- **`pr` and `merge_commit`:** the PR number and its merge commit.
- **`session`:** the Claude Code session ID, taken from the transcript file name.
- **`transcript_sha256` and `transcript_bytes`:** the SHA-256 and length of the transcript when the line was written.
- **`recorded`:** when the line was written.

`scripts/provenance.py add --pr N --transcript <file>` writes the line. It refuses unless the PR is merged and the
transcript contains the `gh pr create` call that returned that PR's URL. As in the orchestration log, a session is
recorded only from that evidence and never inferred.

Transcripts are append-only, so a transcript handed over later still matches over its first `transcript_bytes` bytes.
`scripts/provenance.py verify --transcript <file>` checks this for every line naming that session.
`scripts/check_docs.py` (CI) checks every line on each change:
- each line is complete JSON;
- its PR is merged;
- its merge commit is in history.

## Consequences
- **Verifiable provenance:** a reviewer who receives a transcript can confirm, offline, that it is the one recorded
  for a PR and that it contains the call that created the PR. The repository holds only the hash, never the text,
  which can include prompts and tool output.
- **No back-fill:** PRs merged before this record have no lines. Their sessions stay as written in the orchestration
  log, checkable only on the author's machine, so the cap on those PRs remains.
- **What a line proves:** a session's tool call created the PR. It does not prove that the session wrote every line
  of the change, because a human can push to the same branch. The log and the PR history remain the record of
  who did what.
- **A manual step:** the line is added in a follow-up commit after the merge, the same way rows are added to the
  orchestration log.
