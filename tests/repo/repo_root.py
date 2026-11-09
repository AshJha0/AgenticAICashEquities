"""The repository root for the repository-integrity tier (H5), resolved explicitly.

``CEAP_REPO_ROOT`` wins when set (a test run from an installed package or another checkout);
otherwise walk up from this file to the directory that holds ``pyproject.toml`` and ``scripts/``.
A run that cannot find it fails loudly instead of reading the wrong tree.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType


def _resolve() -> Path:
    env = os.environ.get("CEAP_REPO_ROOT")
    if env:
        root = Path(env).resolve()
        if not (root / "pyproject.toml").is_file():
            raise RuntimeError(f"CEAP_REPO_ROOT={env} has no pyproject.toml")
        return root
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "scripts").is_dir():
            return parent
    raise RuntimeError("repository root not found above tests/repo; set CEAP_REPO_ROOT")


ROOT = _resolve()


def load_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py`` as a module."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
