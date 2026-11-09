"""Download the Ken French files for study E1 into ``data/real_cache/kf/`` (git-ignored) and record
their SHA-256 in ``docs/research/e1/data_hashes.json``. Free, no sign-up (owner decision D2).
Network script; tests never run it.

    python scripts/fetch_kf_industries.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.studies.industry_momentum import CACHE_DIR, KF_FILES, record_hashes  # noqa: E402

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name in KF_FILES:
        with urllib.request.urlopen(BASE + name, timeout=60) as resp:  # noqa: S310 - fixed https URL
            (CACHE_DIR / name).write_bytes(resp.read())
        print("downloaded", name)
    print(record_hashes())


if __name__ == "__main__":
    main()
