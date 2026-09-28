"""Helpers shared by the MCP servers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ceap.data.historical import HistoricalDataset, HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.data.synthetic import LONDON, SyntheticDataset
from ceap.domain.common import ensure_utc

DEFAULT_DATASET = "T01"
DEFAULT_RESEARCH_DATASET = "R01"


def parse_ts(value: str | datetime) -> datetime:
    """Parse an ISO-8601 string (or pass through a datetime) as an aware datetime.

    Naive inputs are interpreted as Europe/London - the desk's local time -
    which is what a trader means by "14:00".
    """
    if isinstance(value, datetime):
        ts = value
    else:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=LONDON)
    return ensure_utc(ts)


def select_dataset(store: DatasetStore, dataset: str | None) -> SyntheticDataset:
    return store.get(dataset or DEFAULT_DATASET)


def select_history(history: HistoricalStore, dataset: str | None) -> HistoricalDataset:
    return history.get(dataset or DEFAULT_RESEARCH_DATASET)


def window_of(
    ds: SyntheticDataset, start: str | datetime | None, end: str | datetime | None
) -> tuple[datetime, datetime]:
    s = parse_ts(start) if start else ds.window_start
    e = parse_ts(end) if end else ds.window_end
    if e <= s:
        raise ValueError("end must be after start")
    return s, e


def paginate(items: list[Any], limit: int, offset: int = 0) -> dict[str, Any]:
    page = items[offset : offset + limit]
    return {"count": len(items), "offset": offset, "limit": limit, "returned": len(page), "items": page}


def downsample(items: list[Any], max_points: int) -> list[Any]:
    if max_points <= 0 or len(items) <= max_points:
        return items
    import numpy as np

    idx = np.linspace(0, len(items) - 1, max_points).round().astype(int)
    return [items[i] for i in dict.fromkeys(idx.tolist())]  # evenly spaced, first and last always kept
