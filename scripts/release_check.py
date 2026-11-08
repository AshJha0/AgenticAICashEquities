"""Gate a release: run by ``release.yml`` on a ``v*`` tag before anything is built.

Checks, in order, and exits non-zero on the first failure:
1. the latest run of the CI workflow on the tagged commit completed with success. Only CI gates:
   the daily paper job, the nightly run and Pages also report on main commits, and a refused
   paper session (a holiday, a run before the forward start) must not block a release;
2. the version agrees in ``pyproject.toml``, ``src/ceap/__init__.py`` and the tag;
3. ``CHANGELOG.md`` has a ``## [<version>]`` section;
4. the banned-word check is clean;
5. every installed distribution's licence is on the allowlist.

Usage: python scripts/release_check.py --tag v0.4.0 --sha <sha> --repo owner/name
Local dry run: add --skip-checks. Step 1 needs ``GH_TOKEN`` (the workflow token is enough).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_LICENCE_MARKERS = (
    "MIT", "BSD", "Apache", "ISC", "PSF", "Python Software Foundation", "MPL", "Mozilla",
    "Unlicense", "Zlib", "HPND", "LGPL", "CC0", "Public Domain", "0BSD",
)  # fmt: skip
#: Distributions whose metadata carries no usable licence string; each checked by hand.
LICENCE_EXCEPTIONS = {
    "cash-equities-agentic-platform": "this project, MIT",
    "pip": "MIT",
    "setuptools": "MIT",
    "wheel": "MIT",
}
#: Workflows whose result gates a release (by workflow name).
GATING_WORKFLOWS = {"CI"}


class ReleaseRefusedError(Exception):
    pass


def evaluate_workflow_runs(runs: list[dict[str, Any]]) -> str:
    """Raise ``ReleaseRefusedError`` unless the latest gating run succeeded; return its description."""
    gating = [r for r in runs if r.get("name") in GATING_WORKFLOWS]
    if not gating:
        raise ReleaseRefusedError("no CI run found for this commit; refusing to release an unverified commit")
    latest = max(gating, key=lambda r: (r.get("created_at") or "", r.get("run_attempt") or 0))
    desc = f"{latest['name']} run {latest.get('id')} ({latest.get('event')})"
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ReleaseRefusedError(
            f"commit is not green: {desc} is {latest.get('status')}/{latest.get('conclusion')}"
        )
    return desc


def check_runs(repo: str, sha: str) -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ReleaseRefusedError("GH_TOKEN not set; cannot verify the CI run")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runs?head_sha={sha}&per_page=100",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
        runs = json.load(resp)["workflow_runs"]
    print(f"ok: {evaluate_workflow_runs(runs)} succeeded on {sha[:12]}")


def versions(tag: str, root: Path = ROOT) -> str:
    py = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    init = (root / "src" / "ceap" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    pkg = m.group(1) if m else None
    tv = tag.removeprefix("v")
    if not (py == pkg == tv):
        raise ReleaseRefusedError(f"version mismatch: pyproject={py} __init__={pkg} tag={tv}")
    print(f"ok: version {py} agrees in pyproject, __init__ and tag")
    return py


def changelog(version: str, root: Path = ROOT) -> None:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    # Accept both "## [0.4.0] - date" and the older house style "## 0.4.0 - title".
    if not re.search(rf"^## \[?{re.escape(version)}\]?(?=[\s]|$)", text, re.M):
        raise ReleaseRefusedError(f"CHANGELOG.md has no '## [{version}]' section")
    print(f"ok: CHANGELOG section for {version}")


def banned_words() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_banned_words.py")], cwd=ROOT)
    if proc.returncode:
        raise ReleaseRefusedError("banned-word check failed")


def _licence(dist: metadata.Distribution) -> str:
    md = dist.metadata
    parts = [md.get("License-Expression") or "", md.get("License") or ""]
    parts += [c for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)


def licences() -> None:
    bad = []
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").lower()
        if name in LICENCE_EXCEPTIONS:
            continue
        lic = _licence(dist)
        if not any(marker.lower() in lic.lower() for marker in ALLOWED_LICENCE_MARKERS):
            bad.append(f"{name}: {lic[:80] or 'UNKNOWN'}")
    if bad:
        raise ReleaseRefusedError("licences not on the allowlist:\n  " + "\n  ".join(sorted(bad)))
    print("ok: every installed distribution is on the licence allowlist")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sha", default="")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--skip-checks", action="store_true", help="local dry run: skip the check-run query")
    ap.add_argument("--skip-licences", action="store_true")
    args = ap.parse_args()
    try:
        if not args.skip_checks:
            check_runs(args.repo, args.sha)
        version = versions(args.tag)
        changelog(version)
        banned_words()
        if not args.skip_licences:
            licences()
    except ReleaseRefusedError as exc:
        print(f"RELEASE REFUSED: {exc}", file=sys.stderr)
        return 1
    print("release check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
