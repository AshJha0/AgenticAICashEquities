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
    order_ttl_days: int = 5
    cost_multiplier: float = 1.0
    fee_per_share: float = 0.0
    min_fee: float = 0.0
    in_sample_sharpe: float | None = None
    db_path: str = "var/paper/paper.db"

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
            order_ttl_days=self.order_ttl_days,
        )

    def with_(self, **kw: Any) -> PaperConfig:
        return replace(self, **kw)


def load_config(path: str | Path | None = None) -> PaperConfig:
    p = Path(path or os.getenv("CEAP_PAPER_CONFIG") or DEFAULT_PATH)
    if not p.exists():
        return PaperConfig()
    raw = json.loads(p.read_text(encoding="utf-8"))
    known = {f.name for f in fields(PaperConfig)}
    return PaperConfig(**{k: v for k, v in raw.items() if k in known})
