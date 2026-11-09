# 0014 · Image and code scanning in CI

**Status:** Accepted 2026-10-31.

## Context
Nothing checked the service images or the code for known vulnerabilities. The first Trivy scan of the images
built from `main` found 59 fixable HIGH/CRITICAL Debian findings per image (the `python:3.13.13-slim` tag was no
longer rebuilt) and 37 more in Python packages; they were cleared in #95 and #98.

## Decision
- `.github/workflows/security.yml` builds each service image (gateway, worker, seed, audit) and runs Trivy
  (`aquasec/trivy:0.76.1`, `--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1`). A fixable HIGH or CRITICAL
  finding fails the pull request.
- CodeQL analyses Python and JavaScript/TypeScript; results go to the repository's code-scanning alerts.
- The workflow also runs weekly, because new CVEs are published against code that has not changed.

## Consequences
- A newly published CVE can turn a pull request red without any change in it; the fix is a dependency or base
  image bump in its own change.
- Unfixed CVEs are not gated: there is nothing to upgrade to. The frontend's Vite dev server image is not
  scanned; it is a development container, not a deployable artifact.
- CodeQL does not fail the build; its alerts are triaged in the Security tab.

## Addendum 2026-11-03: the frontend image is scanned, and CodeQL alerts have an owner
- The frontend's dev image joins the Trivy matrix (`images (frontend)`). Its first scan found 7 fixable
  HIGH/CRITICAL Debian findings (`perl-base`, fixed by `node:24.22.0-slim` → `24.22.2-slim`) and 6 in the npm
  bundled with Node (`brace-expansion` 5.0.9, `undici` 6.28.0). npm 11.21.1 fixes `brace-expansion`, but every
  npm release up to 12.2.1 bundles `undici` 6.29.0, still below the fixed 6.29.1. The image needs npm only to
  install, so it removes npm after `npm ci` and runs Vite directly; the scan is then clean. CI's frontend job
  uses the same Node patch.
- `SECURITY.md` names the maintainer as owner of CodeQL and scheduled-scan triage, with response times by
  severity and the rule that a dismissal carries a reason specific to the flagged code.

## Addendum 2026-11-09: zlib1g upgraded in the Python images
- The weekly scan found two fixable HIGH CVEs in `zlib1g` (CVE-2026-76844, CVE-2026-85091) in all four Python
  images. `python:3.13.18-slim` still ships `1:1.3.dfsg+really1.3.1-1+b1`, and Debian fixed them in
  `1:1.3.dfsg+really1.3.1-1+deb13u1`. Each runtime stage upgrades only `zlib1g` from Debian, so the rest of the
  image stays as the tag ships it. The upgrade can go once a rebuilt tag carries the fix.
