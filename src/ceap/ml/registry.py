"""Model registry and model cards (PLAN 2B.5).

Every fitted model in CEAP is fitted to *synthetic* data only. A model is stored as one JSON file
``data/models/<name>-<model_hash>.json`` holding its card and its parameters, and is loaded by
hash: the loader recomputes the hash from the stored parameters and refuses a mismatch.

Hashes
    ``model_hash``         content hash of ``{name, kind, params}`` with floats rounded to
                           :data:`HASH_DECIMALS` places, so a last-bit BLAS difference across OS or
                           thread count does not change the identity of a model;
    ``training_data_hash`` supplied by the trainer (content hash of what it was fitted on);
    ``generator_hash``     :func:`generator_hash`, the hash of the synthetic generator sources.

Every card carries the label ``"fitted to synthetic generator <generator_hash>"`` and every output
of a model carries its ``model_hash``.

Drift
    A card may hold a feature reference (decile edges and the expected share per bin). At load
    with live features (``ModelRegistry.load(name, features=...)``), :meth:`ModelRegistry.check_drift`
    computes the population stability index of those features against that reference and emits a
    :class:`DriftWarning` above :data:`PSI_WARN`; the values are kept on ``RegisteredModel.drift``.
    0.10 (Q7, findings Q-10, R-17): the change detector's loader passes the live reference window,
    so the check runs in production, not only in tests.

Staleness
    Every load compares the card's generator hash with :func:`generator_hash` and emits a
    :class:`StaleModelWarning` on a mismatch: the model was fitted to a generator that no longer
    exists. A unit test fails for any stored model in that state, so a generator change forces a
    retrain (``python scripts/train_ml_models.py``).
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from ceap.util.canonical import canonical_text, content_hash, sha256_hex

MODELS_DIR = Path(__file__).resolve().parents[3] / "data" / "models"
HASH_DECIMALS = 8
PSI_WARN = 0.25  # conventional: < 0.1 stable, 0.1 to 0.25 moderate shift, > 0.25 significant shift
PSI_BINS = 10
_PSI_EPS = 1e-4

#: generator sources whose bytes define "the synthetic generator" a model was fitted to
GENERATOR_MODULES = (
    "ceap.data.synthetic",
    "ceap.data.scenarios",
    "ceap.data.historical",
    "ceap.data.generator_params",
    "ceap.data.research_scenarios",
)


class DriftWarning(UserWarning):
    """Live features have drifted from the model's training reference (PSI above threshold)."""


class StaleModelWarning(UserWarning):
    """The model was fitted to a synthetic generator whose sources have since changed."""


class ModelIntegrityError(RuntimeError):
    """The stored parameters do not reproduce the hash the model is addressed by."""


@lru_cache(maxsize=1)
def generator_hash() -> str:
    """Short content hash of the synthetic generator sources (line endings normalised)."""
    import importlib.util

    parts = []
    for mod in GENERATOR_MODULES:
        spec = importlib.util.find_spec(mod)
        if spec is None or spec.origin is None:
            raise RuntimeError(f"generator module {mod} not found")
        parts.append(sha256_hex(canonical_text(Path(spec.origin).read_bytes())))
    return sha256_hex("\n".join(parts).encode("utf-8"))[:16]


def synthetic_label(kind: str = "fitted") -> str:
    """``"fitted to synthetic generator <hash>"`` (or ``calibrated``)."""
    return f"{kind} to synthetic generator {generator_hash()}"


def _round(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("model parameters must be finite")
        r = round(value, HASH_DECIMALS)
        return 0.0 if r == 0 else r  # -0.0 and 0.0 hash alike
    if isinstance(value, np.ndarray):
        return _round(value.tolist())
    if isinstance(value, np.generic):
        return _round(value.item())
    if isinstance(value, dict):
        return {str(k): _round(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_round(v) for v in value]
    return value


def model_hash(name: str, kind: str, params: dict[str, Any]) -> str:
    return content_hash({"name": name, "kind": kind, "params": _round(params)})[:16]


# ------------------------------------------------------------------ drift (PSI)
def feature_reference(values: np.ndarray, bins: int = PSI_BINS) -> dict[str, list[float]]:
    """Decile edges of the training feature and the share of training rows in each bin."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < bins:
        raise ValueError("too few values for a feature reference")
    edges = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1)[1:-1]))
    counts = np.bincount(np.searchsorted(edges, x, side="right"), minlength=edges.size + 1)
    return {"edges": edges.tolist(), "expected": (counts / counts.sum()).tolist()}


def psi(reference: dict[str, list[float]], values: np.ndarray) -> float:
    """Population stability index ``sum (a - e) ln(a / e)`` over the reference bins."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan")
    edges = np.asarray(reference["edges"], dtype=float)
    expected = np.clip(np.asarray(reference["expected"], dtype=float), _PSI_EPS, None)
    counts = np.bincount(np.searchsorted(edges, x, side="right"), minlength=edges.size + 1)
    actual = np.clip(counts / counts.sum(), _PSI_EPS, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


# ------------------------------------------------------------------ cards
@dataclass(frozen=True)
class ModelCard:
    name: str
    kind: str
    model_hash: str
    training_data_hash: str
    generator_hash: str
    label: str
    created: str
    metrics: dict[str, Any]
    description: str = ""
    intended_use: str = ""
    limitations: str = ""
    sklearn_version: str | None = None
    feature_reference: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    # 0.10 D22 model-risk inventory fields (SR 11-7 style); defaults keep pre-0.10 card files loadable
    owner: str = "CEAP maintainers"
    validator: str = "unassigned (no independent validation yet)"
    validated_on: str = ""
    status: str = "research"  # research | approved | retired
    tier: str = "3"  # 1 = drives orders, 2 = shown to an approver, 3 = research only
    review_cadence_days: int = 365
    challenger: str = ""  # the naive model this one must beat

    def to_markdown(self) -> str:
        rows = "\n".join(f"| {k} | {v} |" for k, v in sorted(self.metrics.items()))
        return (
            f"## {self.name} (`{self.model_hash}`)\n\n"
            f"*{self.label}.* Kind `{self.kind}`; created {self.created}; training data `{self.training_data_hash}`"
            + (f"; scikit-learn {self.sklearn_version}" if self.sklearn_version else "")
            + f".\n\n{self.description}\n\n**Intended use.** {self.intended_use}\n\n"
            f"**Limitations.** {self.limitations}\n\n"
            f"**Inventory.** Owner {self.owner}; validator {self.validator}"
            + (f" ({self.validated_on})" if self.validated_on else "")
            + f"; status {self.status}; tier {self.tier}; review every {self.review_cadence_days} days"
            + (f"; challenger: {self.challenger}" if self.challenger else "")
            + f".\n\n| Metric | Value |\n|---|---|\n{rows}\n"
        )


@dataclass(frozen=True)
class RegisteredModel:
    card: ModelCard
    params: dict[str, Any]
    drift: dict[str, float] = field(default_factory=dict)  # PSI per feature when loaded with live features

    @property
    def model_hash(self) -> str:
        return self.card.model_hash


class ModelRegistry:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else MODELS_DIR

    def path_for(self, name: str, mhash: str) -> Path:
        return self.root / f"{name}-{mhash}.json"

    def register(
        self,
        name: str,
        kind: str,
        params: dict[str, Any],
        *,
        training_data_hash: str,
        metrics: dict[str, Any],
        description: str = "",
        intended_use: str = "",
        limitations: str = "",
        feature_reference: dict[str, dict[str, list[float]]] | None = None,
        created: str | None = None,
        label_kind: str = "fitted",
    ) -> RegisteredModel:
        try:
            import sklearn

            skv: str | None = sklearn.__version__
        except ImportError:  # pragma: no cover - the ml extra is installed in dev and CI
            skv = None
        mhash = model_hash(name, kind, params)
        card = ModelCard(
            name=name,
            kind=kind,
            model_hash=mhash,
            training_data_hash=training_data_hash,
            generator_hash=generator_hash(),
            label=synthetic_label(label_kind),
            created=created or date.today().isoformat(),
            metrics=_round(metrics),
            description=description,
            intended_use=intended_use,
            limitations=limitations,
            sklearn_version=skv,
            feature_reference=feature_reference or {},
        )
        model = RegisteredModel(card, _round(params))
        self.root.mkdir(parents=True, exist_ok=True)
        body = {"card": asdict(card), "params": model.params}
        self.path_for(name, mhash).write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return model

    def load(
        self, name: str, mhash: str | None = None, features: dict[str, np.ndarray] | None = None
    ) -> RegisteredModel:
        """Load by hash (or the only stored version of ``name``); recompute and verify the hash; warn when the
        generator has changed since fitting; with live ``features``, run the drift check (PSI) at load."""
        if mhash is None:
            found = sorted(self.root.glob(f"{name}-*.json"))
            if len(found) != 1:
                raise LookupError(f"{len(found)} stored versions of {name!r}; load by hash")
            path = found[0]
        else:
            path = self.path_for(name, mhash)
        body = json.loads(path.read_text(encoding="utf-8"))
        card = ModelCard(**body["card"])
        params = body["params"]
        if model_hash(card.name, card.kind, params) != card.model_hash or (mhash and mhash != card.model_hash):
            raise ModelIntegrityError(f"{path.name}: stored parameters do not reproduce hash {card.model_hash}")
        if card.generator_hash != generator_hash():
            warnings.warn(
                f"{card.name} ({card.model_hash}) was fitted to generator {card.generator_hash}; the current "
                f"generator is {generator_hash()}: retrain with scripts/train_ml_models.py",
                StaleModelWarning,
                stacklevel=2,
            )
        model = RegisteredModel(card, params)
        if features is not None:
            model = RegisteredModel(card, params, self.check_drift(model, features))
        return model

    def list(self) -> list[ModelCard]:
        return [ModelCard(**json.loads(p.read_text(encoding="utf-8"))["card"]) for p in sorted(self.root.glob("*.json"))]

    @staticmethod
    def check_drift(
        model: RegisteredModel, features: dict[str, np.ndarray], threshold: float = PSI_WARN
    ) -> dict[str, float]:
        """PSI per referenced feature; warns with :class:`DriftWarning` for any above ``threshold``."""
        out = {k: psi(ref, features[k]) for k, ref in model.card.feature_reference.items() if k in features}
        drifted = {k: v for k, v in out.items() if math.isfinite(v) and v > threshold}
        if drifted:
            warnings.warn(
                f"{model.card.name} ({model.model_hash}): feature drift PSI "
                + ", ".join(f"{k}={v:.3f}" for k, v in sorted(drifted.items()))
                + f" above {threshold}",
                DriftWarning,
                stacklevel=2,
            )
        return out
