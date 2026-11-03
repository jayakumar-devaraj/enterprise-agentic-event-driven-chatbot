# Security scanning and triage

What runs is in ADR 0014 (`docs/adr/0014-security-scans-in-ci.md`): Trivy on every image, which fails a pull
request on a fixable HIGH or CRITICAL finding, and CodeQL, which never fails one. This file says who acts on
the results and how fast.

## Owner
Jayakumar Devaraj, the repository maintainer, triages every code-scanning alert (Security → Code scanning) and
every Trivy failure on the weekly scheduled run. GitHub notifies the maintainer of new alerts.

## Response times
Counted from when the alert opens or the scheduled run fails:

| Severity | Triaged (fixed, or dismissed with a reason) |
|---|---|
| Critical, High (CodeQL `error`) | 7 days |
| Medium (CodeQL `warning`) | 30 days |
| Low, note | at the next change to the affected file |

## Triage
- **Fix** in a pull request that names the alert number. The alert closes when the fix reaches `main`.
- **Dismiss** only with GitHub's reason ("false positive", "won't fix", "used in tests") and a comment that
  says why the code is safe, specific to the flagged line. A dismissal without that comment is reopened.
- **A Trivy finding with no fixed release** is not gated (`--ignore-unfixed`). When a fix exists but no
  dependency has picked it up, remove the affected component from the image if it is not needed at run time,
  as the frontend image does with npm; otherwise record the exception in `docs/known_limitations.md` with the
  CVE ids and the date to look again.
