"""0.10.0 X5 (finding R-23): every document that describes ceap.execution says what it is."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABEL = "simulator library, not used by the default paper book"
DOCS = ["README.md", "LEARN.md", "COOKBOOK.md", "docs/INDEX.md", "docs/DIAGRAMS.md",
        "docs/execution/execution.md", "src/ceap/execution/__init__.py"]


def test_every_execution_document_carries_the_label() -> None:
    missing = [d for d in DOCS if LABEL not in (ROOT / d).read_text(encoding="utf-8")]
    assert not missing, f"add the ceap.execution status label to: {missing}"
