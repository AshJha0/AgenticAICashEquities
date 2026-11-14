"""Where the platform's data lives (R-27, E3).

The code used to resolve ``data/`` as ``Path(__file__).parents[3] / "data"``, which only exists in a
source checkout: installed into ``site-packages`` the path points nowhere, and the knowledge base,
the change detector and the calibration maps fell back silently. One resolver now decides, in this
order:

1. ``CEAP_DATA_ROOT`` when set (a deployment that mounts its own data);
2. the copy shipped inside the wheel at ``ceap/_data`` (``setup.py`` copies the runtime files
   listed in :data:`PACKAGED` when the wheel is built);
3. ``data/`` at the root of a source checkout (development, editable installs, the test suite).

Optional inputs keep their documented fallbacks. Inputs the platform cannot run correctly without
go through :func:`require`, which raises :class:`DataFileMissing` naming the root it searched and
the variable that overrides it, instead of carrying on with an empty knowledge base or an unfitted
model.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_ENV = "CEAP_DATA_ROOT"
PACKAGE_DATA = Path(__file__).resolve().parent / "_data"
CHECKOUT_DATA = Path(__file__).resolve().parents[2] / "data"

#: data-root-relative globs shipped in the wheel: everything a running service reads. Raw vendor
#: caches (``real_cache``) and generated exports are never shipped.
PACKAGED: tuple[str, ...] = (
    "reference/knowledge/*.md",
    "reference/knowledge/*.json",
    "reference/*.json",
    "reference/*.jsonl",
    "reference/ATTRIBUTION.md",
    "reference/nyse_calendar.csv",
    "reference/rf.csv",
    "reference/ff_factors.csv",
    "reference/sp500_changes.csv",
    "reference/ticker_map.csv",
    "models/*.json",
    "rag/*.json",
    "eval/*.json",
)

#: inputs without which an investigation is wrong rather than degraded (checked by ``ceap.smoke``)
REQUIRED: tuple[str, ...] = ("reference/knowledge", "models")


class DataFileMissing(RuntimeError):  # noqa: N818 - reads as a sentence at the raise site
    """A required data file or directory is absent from the resolved data root.

    Deliberately not an ``OSError``: callers that catch ``OSError`` to fall back must not swallow it.
    """


def data_root() -> Path:
    """The directory data paths are resolved against (see the module docstring for the order)."""
    override = os.environ.get(DATA_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if PACKAGE_DATA.is_dir():
        return PACKAGE_DATA
    return CHECKOUT_DATA


def data_path(*parts: str) -> Path:
    """``data_root() / parts`` without checking that it exists (optional inputs and outputs)."""
    return data_root().joinpath(*parts)


def require(*parts: str) -> Path:
    """``data_path(*parts)``, raising :class:`DataFileMissing` when it does not exist."""
    path = data_path(*parts)
    if not path.exists():
        raise DataFileMissing(
            f"required data {'/'.join(parts)!r} not found under {data_root()} "
            f"(set {DATA_ENV} to a directory holding the platform's data/ tree)"
        )
    return path
