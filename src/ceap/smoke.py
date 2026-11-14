"""Installed-copy smoke check: ``python -m ceap.smoke`` (R-27, E3).

Run from an installed wheel or inside the container image, outside any source checkout. It runs one
investigation with the offline mock model and fails (exit 1) unless the knowledge base and the
registered models were loaded from the resolved data root, so a package that silently lost its data
cannot pass the way ``ceap --help`` did.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


def run() -> dict[str, Any]:
    """Run the check and return what it saw; raises ``AssertionError`` or ``DataFileMissing`` on failure."""
    from ceap.analytics.calibration import CHANGE_POINT_KEY, calibration_info
    from ceap.config import Settings
    from ceap.llm import MockLLMClient
    from ceap.ml.anomaly import load_detector
    from ceap.paths import REQUIRED, data_root, require
    from ceap.platform import Platform, default_request

    for rel in REQUIRED:
        require(*rel.split("/"))
    knowledge = require("reference", "knowledge")
    manifest = knowledge / "manifest.json"
    external = json.loads(manifest.read_text(encoding="utf-8")).get("external", []) if manifest.exists() else []
    expected_documents = len(list(knowledge.glob("*.md"))) + len(external)
    detector = load_detector()
    calibration = calibration_info(CHANGE_POINT_KEY)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(llm_provider="mock", auto_approve=True, db_path=str(Path(tmp) / "smoke.db"))
        platform = Platform(settings=settings, llm=MockLLMClient())
        try:
            result = asyncio.run(platform.investigate(default_request()))
            documents = len(platform.knowledge.documents)
        finally:
            platform.close()
    seen = {
        "data_root": str(data_root()),
        "knowledge_documents": documents,
        "detector_model_hash": detector.model_hash,
        "calibration_model_hash": calibration["model_hash"],
        "state": result.state.value,
        "report": result.report is not None,
    }
    # every corpus document, including the manifest's external ones (a skipped source only logs)
    assert documents > 0 and documents == expected_documents, f"knowledge base incomplete: {seen}"
    assert detector.model_hash, f"change detector not loaded from the registry: {seen}"
    assert calibration["model_hash"], f"calibration model not loaded from the registry: {seen}"
    assert result.state.value == "COMPLETED" and result.report is not None, f"investigation failed: {seen}"
    return seen


def main() -> int:
    try:
        seen = run()
    except Exception as exc:  # noqa: BLE001 - the smoke check reports any failure as one line and exit 1
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(seen, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CI wheel and image jobs
    raise SystemExit(main())
