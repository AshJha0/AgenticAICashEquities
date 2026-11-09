"""Predicted-versus-realised cost loop (F6).

Every parent gets a pre-trade cost prediction before it trades and a realised Perold shortfall
after. The loop keeps both, recalibrates the impact model on the residuals and says when it has
drifted:

* **Prediction** - the square-root law ``Y * sigma_daily * (Q/ADV)^delta`` in bps. With ``delta = 0.5``
  it is exactly :func:`ceap.analytics.market_impact.square_root_impact_estimate_bps` (the function
  is called, not copied). An Almgren-Chriss expected cost can be stored instead through
  ``predicted_bps``; the loop does not care where the prediction came from.
* **Storage** - one SQLite table of observations (``:memory:`` or a file), so the loop survives a
  restart and can be inspected with any SQLite tool.
* **Recalibration** - least squares of ``realised / sigma`` on ``(Q/ADV)^delta`` over a grid of
  exponents; ``Y`` has a closed form for each ``delta``. Run it weekly (:meth:`CostLoop.weekly`).
* **Drift alert** - the mean residual of the latest window in standard errors; ``|z| >= 2`` alerts.
* **Reliability** - predicted against realised by prediction decile, with standard errors, which is
  the table a reliability plot is drawn from.

Evidence class: the square-root law is an empirical regularity (Almgren, Thum, Hauptmann and Li
(2005), "Direct estimation of equity market impact", Risk 18(7); Toth et al. (2011), Phys. Rev. X
1, 021006); the default ``Y = 0.6`` is the repo's existing pre-trade coefficient, not a fit.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from ceap.analytics.market_impact import square_root_impact_estimate_bps

DEFAULT_Y = 0.6
DEFAULT_DELTA = 0.5


def predict_bps(
    quantity: float, adv: float, sigma_daily_bps: float, y: float = DEFAULT_Y, delta: float = DEFAULT_DELTA
) -> float:
    if delta == 0.5:
        return square_root_impact_estimate_bps(quantity, adv, sigma_daily_bps, y)
    if quantity < 0 or adv <= 0 or not math.isfinite(sigma_daily_bps):
        return float("nan")
    return float(y * sigma_daily_bps * (quantity / adv) ** delta)


@dataclass(frozen=True)
class CostObservation:
    trade_date: date
    order_id: str
    quantity: float
    adv: float
    sigma_daily_bps: float
    predicted_bps: float
    realised_bps: float

    @property
    def participation(self) -> float:
        return self.quantity / self.adv

    @property
    def residual_bps(self) -> float:
        return self.realised_bps - self.predicted_bps


@dataclass(frozen=True)
class Calibration:
    y: float
    delta: float
    y_se: float
    n: int
    rmse_bps: float


@dataclass(frozen=True)
class DriftReport:
    mean_residual_bps: float
    se_bps: float
    z: float
    n: int
    alert: bool


_SCHEMA = """CREATE TABLE IF NOT EXISTS cost_observations (
    order_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    quantity REAL NOT NULL,
    adv REAL NOT NULL,
    sigma_daily_bps REAL NOT NULL,
    predicted_bps REAL NOT NULL,
    realised_bps REAL NOT NULL
)"""


class CostLoop:
    def __init__(self, path: str = ":memory:", y: float = DEFAULT_Y, delta: float = DEFAULT_DELTA) -> None:
        self.db = sqlite3.connect(path)
        self.db.execute(_SCHEMA)
        self.y = y
        self.delta = delta

    def close(self) -> None:
        self.db.close()

    def predict(self, quantity: float, adv: float, sigma_daily_bps: float) -> float:
        return predict_bps(quantity, adv, sigma_daily_bps, self.y, self.delta)

    def record(self, obs: CostObservation) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cost_observations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                obs.order_id,
                obs.trade_date.isoformat(),
                obs.quantity,
                obs.adv,
                obs.sigma_daily_bps,
                obs.predicted_bps,
                obs.realised_bps,
            ),
        )
        self.db.commit()

    def observations(self, start: date | None = None, end: date | None = None) -> list[CostObservation]:
        rows = self.db.execute(
            "SELECT trade_date, order_id, quantity, adv, sigma_daily_bps, predicted_bps, realised_bps "
            "FROM cost_observations WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date, order_id",
            ((start or date.min).isoformat(), (end or date.max).isoformat()),
        ).fetchall()
        return [CostObservation(date.fromisoformat(r[0]), *r[1:]) for r in rows]

    def weekly(
        self, as_of: date, lookback_weeks: int = 12, drift_window_days: int = 7
    ) -> tuple[Calibration, DriftReport]:
        """The weekly job: recalibrate on the lookback, check drift on the latest week, adopt the fit."""
        from datetime import timedelta

        obs = self.observations(as_of - timedelta(weeks=lookback_weeks), as_of)
        cal = recalibrate(obs)
        drift = drift_report(self.observations(as_of - timedelta(days=drift_window_days - 1), as_of))
        if math.isfinite(cal.y):
            self.y, self.delta = cal.y, cal.delta
        return cal, drift


def recalibrate(obs: Iterable[CostObservation], deltas: Sequence[float] | None = None) -> Calibration:
    """Fit ``realised / sigma = Y (Q/ADV)^delta`` by least squares over a grid of ``delta``."""
    rows = [o for o in obs if o.adv > 0 and o.sigma_daily_bps > 0 and math.isfinite(o.realised_bps)]
    nan = float("nan")
    if len(rows) < 3:
        return Calibration(nan, nan, nan, len(rows), nan)
    part = np.array([o.participation for o in rows])
    sig = np.array([o.sigma_daily_bps for o in rows])
    y = np.array([o.realised_bps for o in rows]) / sig
    best: tuple[float, float, float] | None = None
    for d in deltas if deltas is not None else np.round(np.arange(0.2, 1.01, 0.05), 2):
        x = part ** float(d)
        coef = float((x * y).sum() / (x * x).sum())
        sse = float(((y - coef * x) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, float(d), coef)
    assert best is not None
    sse, d, coef = best
    x = part**d
    n = len(rows)
    y_se = math.sqrt(sse / max(n - 2, 1) / float((x * x).sum()))
    rmse = math.sqrt(float((((y - coef * x) * sig) ** 2).mean()))
    return Calibration(coef, d, y_se, n, rmse)


def drift_report(obs: Sequence[CostObservation], z_alert: float = 2.0) -> DriftReport:
    r = np.array([o.residual_bps for o in obs if math.isfinite(o.residual_bps)])
    if r.size < 2:
        return DriftReport(
            float(r.mean()) if r.size else float("nan"), float("nan"), float("nan"), int(r.size), False
        )
    se = float(r.std(ddof=1) / math.sqrt(r.size))
    z = float(r.mean() / se) if se > 0 else float("nan")
    return DriftReport(float(r.mean()), se, z, int(r.size), bool(math.isfinite(z) and abs(z) >= z_alert))


def reliability_table(obs: Sequence[CostObservation], bins: int = 10) -> list[dict[str, float]]:
    """Mean predicted vs mean realised (with SE) by prediction quantile bin."""
    rows = [o for o in obs if math.isfinite(o.predicted_bps) and math.isfinite(o.realised_bps)]
    if not rows:
        return []
    p = np.array([o.predicted_bps for o in rows])
    r = np.array([o.realised_bps for o in rows])
    order = np.argsort(p, kind="stable")
    out = []
    for chunk in np.array_split(order, min(bins, len(rows))):
        if chunk.size == 0:
            continue
        rr = r[chunk]
        out.append(
            {
                "n": float(chunk.size),
                "predicted_bps": float(p[chunk].mean()),
                "realised_bps": float(rr.mean()),
                "realised_se_bps": float(rr.std(ddof=1) / math.sqrt(chunk.size))
                if chunk.size > 1
                else float("nan"),
            }
        )
    return out
