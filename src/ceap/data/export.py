"""Export synthetic datasets to CSV / Parquet under ``data/`` (optional).

Parquet needs ``pyarrow`` (``pip install .[quant]``); CSV always works.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ceap.data.synthetic import SyntheticDataset
from ceap.domain.common import to_jsonable


def dataset_frames(ds: SyntheticDataset) -> dict[str, pd.DataFrame]:
    books = [
        {
            "timestamp": b.timestamp,
            "symbol": b.symbol,
            "side": lv.side,
            "level": lv.level,
            "price": lv.price,
            "quantity": lv.quantity,
        }
        for b in ds.order_books
        for lv in b.levels
    ]
    return {
        "market/quotes": pd.DataFrame([to_jsonable(q) for q in ds.quotes]),
        "market/trades": pd.DataFrame([to_jsonable(t) for t in ds.trades]),
        "market/order_book": pd.DataFrame(books),
        "orders/orders": pd.DataFrame([to_jsonable(o) for o in ds.orders]),
        "executions/executions": pd.DataFrame([to_jsonable(e) for e in ds.executions]),
        "reference/metrics": pd.DataFrame([to_jsonable(m) for m in ds.metrics]),
        "reference/logs": pd.DataFrame([to_jsonable(entry) for entry in ds.logs]),
        "reference/deployments": pd.DataFrame([to_jsonable(d) for d in ds.deployments]),
    }


def export_dataset(ds: SyntheticDataset, root: Path, fmt: str = "csv") -> list[Path]:
    written: list[Path] = []
    for rel, frame in dataset_frames(ds).items():
        path = root / f"{rel}_{ds.scenario.id}_{ds.symbol}.{fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "parquet":
            frame.to_parquet(path, index=False)
        else:
            frame.to_csv(path, index=False)
        written.append(path)
    return written
