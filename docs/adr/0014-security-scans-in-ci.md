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
