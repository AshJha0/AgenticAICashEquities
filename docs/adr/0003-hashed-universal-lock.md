# ADR 0003: One universal, hashed dependency lock

- Status: Accepted (0.6.0). Recorded 0.9.0.

## Context

Results in this repository are only reproducible if the same package versions are installed
everywhere: CI on Linux, contributors on Windows, the Docker image. Unpinned or per-platform locks
drift, and an unhashed lock lets a compromised index serve different bytes under the same version.

## Decision

`requirements.lock` is produced by `uv pip compile pyproject.toml --extra all --universal
--generate-hashes --python-version 3.11` and is never edited by hand. Every install uses
`pip install --require-hashes -r requirements.lock`. The CI `lock-fresh` job regenerates the lock
and fails if it differs from the committed file by one byte.

## Consequences

- One file serves Linux, Windows and macOS (universal markers) and every Python in the support range.
- Adding a dependency means editing `pyproject.toml` and regenerating; a hand edit fails CI.
- Tools used by a single CI job only (mutmut, pytest-benchmark, schemathesis, pdoc, cyclonedx-bom,
  pre-commit) are installed in that job at an exact version and kept out of the lock, so the runtime
  lock stays small. This is a deliberate exception: those jobs produce reports, not results.

## Evidence

- `.github/workflows/ci.yml` jobs `lock-fresh` and `audit` (pip-audit against the lock).
- `.github/dependabot.yml` proposes grouped updates; each still has to pass `lock-fresh`.
