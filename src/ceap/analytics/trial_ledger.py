"""Append-only trial ledger (2.8): every distinct backtest configuration on a dataset is one trial.

The ledger is written by the platform's backtest tool, server side; callers
never pass a trial count. A re-run of identical parameters on the same dataset
does not add a trial. ``summary`` returns the count and the cross-trial
variance of the per-day Sharpe ratio, which the deflated Sharpe ratio needs.

Persistence: JSON lines at ``CEAP_TRIAL_LEDGER`` when set (append-only, one
record per line, never rewritten); otherwise in memory for the process.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ceap.util.canonical import content_hash


@dataclass(frozen=True)
class TrialSummary:
    dataset_hash: str
    n_trials: int
    sharpe_variance: float  # variance of the per-day Sharpe across trials (NaN below two trials)
    params_hash: str
    new_trial: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_hash": self.dataset_hash,
            "n_trials": self.n_trials,
            "sharpe_variance": self.sharpe_variance,
            "params_hash": self.params_hash,
            "new_trial": self.new_trial,
        }


class TrialLedger:
    def __init__(self, path: str | Path | None = None) -> None:
        env = os.environ.get("CEAP_TRIAL_LEDGER")
        self._path = Path(path) if path else (Path(env) if env else None)
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        if self._path and self._path.exists():
            with self._path.open(encoding="utf-8") as fh:
                self._records = [json.loads(line) for line in fh if line.strip()]

    @staticmethod
    def params_hash(params: dict[str, Any]) -> str:
        return content_hash({k: params[k] for k in sorted(params)})

    def record(self, dataset_hash: str, params: dict[str, Any], sharpe_daily: float) -> TrialSummary:
        ph = self.params_hash(params)
        with self._lock:
            seen = any(r["dataset_hash"] == dataset_hash and r["params_hash"] == ph for r in self._records)
            if not seen:
                rec = {
                    "dataset_hash": dataset_hash,
                    "params_hash": ph,
                    "params": params,
                    "sharpe_daily": sharpe_daily if sharpe_daily == sharpe_daily else None,
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                self._records.append(rec)
                if self._path:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with self._path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec, sort_keys=True) + "\n")
            return self._summary(dataset_hash, ph, not seen)

    def summary(self, dataset_hash: str) -> TrialSummary:
        with self._lock:
            return self._summary(dataset_hash, "", False)

    def _summary(self, dataset_hash: str, ph: str, new: bool) -> TrialSummary:
        rows = [r for r in self._records if r["dataset_hash"] == dataset_hash]
        sharpes = [float(r["sharpe_daily"]) for r in rows if r.get("sharpe_daily") is not None]
        var = float("nan")
        if len(sharpes) >= 2:
            mean = sum(sharpes) / len(sharpes)
            var = sum((s - mean) ** 2 for s in sharpes) / (len(sharpes) - 1)
        return TrialSummary(dataset_hash, len(rows), var, ph, new)

    def __len__(self) -> int:
        return len(self._records)
