"""Paper-trading configuration (``config/paper.json``; ``CEAP_PAPER_CONFIG`` points elsewhere)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields, replace
from datetime import date
from pathlib import Path
from typing import Any

from ceap.analytics.backtest import CostModel, FinancingModel, PositionCaps
from ceap.analytics.signals import get_signal
from ceap.paper.fills import FillParams
from ceap.paper.pretrade import RiskLimits

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "paper.json"


@dataclass(frozen=True)
class PaperConfig:
    forward_start: str = "2026-11-09"
    feed: str = "synthetic"
    scenario: str = "R01"
    signal: str = "momentum_12_1"
    rebalance_days: int | None = None
    long_short: bool = True
    gross_notional: float = 10_000_000.0
    gross_leverage: float = 1.0
    top_fraction: float = 1 / 3
    weighting: str = "equal"
    fractional_shares: bool = False
    order_ttl_days: int = 0
    cost_multiplier: float = 1.0
    fee_per_share: float = 0.0
    min_fee: float = 0.0
    in_sample_sharpe: float | None = None
    db_path: str = "var/paper/paper.db"
    report_dir: str | None = None  # per-run markdown and JSON artefacts; config/paper.json sets one
    risk: dict[str, Any] | None = None  # RiskLimits fields (see ceap.paper.pretrade)
    # 0.9.0 FillParams execution fields: moc_auction, auction_share, auction_max_fraction, lot_size,
    # engine ("close" | "lob"), lob_window_s (see ceap.paper.fills); absent = the 0.8.0 behaviour
    execution: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.forward_start)
        get_signal(self.signal)
        if self.gross_notional <= 0:
            raise ValueError("gross_notional must be positive")

    @property
    def rebalance(self) -> int:
        return int(self.rebalance_days or get_signal(self.signal).default_rebalance_days)

    def fill_params(self, financing: FinancingModel | None = None) -> FillParams:
        return FillParams(
            costs=CostModel(), financing=financing or FinancingModel(), caps=PositionCaps(),
            cost_multiplier=self.cost_multiplier, fee_per_share=self.fee_per_share, min_fee=self.min_fee,
            order_ttl_days=self.order_ttl_days, risk=RiskLimits.from_json(self.risk),
            **_execution_fields(self.execution),
        )

    def with_(self, **kw: Any) -> PaperConfig:
        return replace(self, **kw)


_EXECUTION_FIELDS = ("moc_auction", "auction_share", "auction_max_fraction", "lot_size", "engine", "lob_window_s")


# panel-side execution keys (not FillParams): dividends (bool, default off), dividend_cache (path),
# dividend_download (bool: refresh the cache from yfinance first; needs the optional package)
_PANEL_FIELDS = ("dividends", "dividend_cache", "dividend_download")


def _execution_fields(d: dict[str, Any] | None) -> dict[str, Any]:
    d = {k: v for k, v in (d or {}).items() if k not in _PANEL_FIELDS}
    unknown = sorted(set(d) - set(_EXECUTION_FIELDS))
    if unknown:
        raise ValueError(f"unknown execution fields {unknown}; known: {list(_EXECUTION_FIELDS)}")
    return d


def load_config(path: str | Path | None = None) -> PaperConfig:
    p = Path(path or os.getenv("CEAP_PAPER_CONFIG") or DEFAULT_PATH)
    if not p.exists():
        return PaperConfig()
    raw = json.loads(p.read_text(encoding="utf-8"))
    known = {f.name for f in fields(PaperConfig)}
    return PaperConfig(**{k: v for k, v in raw.items() if k in known})
