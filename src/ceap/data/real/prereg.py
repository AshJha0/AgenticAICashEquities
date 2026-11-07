"""Preregistration lock (R5 item 5.1).

The preregistration is hashed (SHA-256 of the file bytes with line endings normalised to LF, so a
Windows checkout gives the same hash) and the hash is recorded in ``docs/research/prereg_lock.json``
in the same commit. Price loaders call :func:`require_prereg_lock` and refuse to run on mismatch.
The lock also fixes the date windows, so no loader can read the pseudo-holdout by accident.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
PREREG_PATH = REPO_ROOT / "docs" / "research" / "prereg_momentum_2026-11.md"
LOCK_PATH = REPO_ROOT / "docs" / "research" / "prereg_lock.json"


class PreregistrationError(RuntimeError):
    """The preregistration is missing, edited after the lock, or a date is out of bounds."""


@dataclass(frozen=True)
class PreregLock:
    sha256: str
    warmup_start: date
    eval_start: date
    eval_end: date
    holdout_start: date
    holdout_end: date


def file_sha256(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def load_lock(lock_path: Path = LOCK_PATH) -> PreregLock:
    if not lock_path.exists():
        raise PreregistrationError(f"no preregistration lock at {lock_path}")
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    w = raw["windows"]
    return PreregLock(
        sha256=raw["sha256"],
        warmup_start=date.fromisoformat(w["warmup_start"]),
        eval_start=date.fromisoformat(w["eval_start"]),
        eval_end=date.fromisoformat(w["eval_end"]),
        holdout_start=date.fromisoformat(w["holdout_start"]),
        holdout_end=date.fromisoformat(w["holdout_end"]),
    )


def require_prereg_lock(prereg_path: Path = PREREG_PATH, lock_path: Path = LOCK_PATH) -> PreregLock:
    """Return the lock if the preregistration hashes to the recorded value, else raise."""
    lock = load_lock(lock_path)
    if not prereg_path.exists():
        raise PreregistrationError(f"preregistration missing: {prereg_path}")
    actual = file_sha256(prereg_path)
    if actual != lock.sha256:
        raise PreregistrationError(
            f"preregistration hash mismatch: recorded {lock.sha256}, file {actual}; "
            "price data will not be loaded"
        )
    return lock


def check_in_sample(end: date, lock: PreregLock) -> None:
    """Refuse any read that reaches past the in-sample end (the pseudo-holdout is item 5.7)."""
    if end > lock.eval_end:
        raise PreregistrationError(
            f"requested end {end} is past the in-sample end {lock.eval_end}; "
            "the pseudo-holdout is not readable in this round"
        )
