"""Fail if any tracked file contains a banned word (owner rule for this public repository).

Usage: python scripts/check_banned_words.py   (exit 1 and list hits when any are found)
The word list is assembled from fragments so this file does not match itself.
"""

from __future__ import annotations

import subprocess
import sys

BANNED = ["inter" + "view"]


def hits() -> list[str]:
    out: list[str] = []
    for word in BANNED:
        proc = subprocess.run(
            ["git", "grep", "-n", "-i", "-I", word], capture_output=True, text=True, check=False
        )
        if proc.returncode not in (0, 1):
            raise SystemExit(f"git grep failed: {proc.stderr.strip()}")
        out.extend(line for line in proc.stdout.splitlines() if line)
    return out


def main() -> int:
    found = hits()
    for line in found:
        print(line)
    if found:
        print(f"banned-word check: {len(found)} hit(s)", file=sys.stderr)
        return 1
    print("banned-word check: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
