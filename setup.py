"""Build hook only; all metadata lives in pyproject.toml.

R-27 (E3): the runtime data (knowledge corpus, registered models, retrieval caches, reference
tables) sits in ``data/`` at the repository root, outside the package, so a plain wheel shipped
without it. ``build_py`` copies the files matched by ``ceap.paths.PACKAGED`` into
``ceap/_data`` inside the build tree; ``ceap.paths.data_root()`` finds them there once installed.
The source tree is never written to, so a checkout keeps reading ``data/`` directly.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


def _packaged_globs() -> tuple[str, ...]:
    """``PACKAGED`` read from ``src/ceap/paths.py`` without importing the package (no deps at build time)."""
    tree = ast.parse((ROOT / "src" / "ceap" / "paths.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "PACKAGED":
            return tuple(ast.literal_eval(node.value))  # type: ignore[arg-type]
    raise RuntimeError("PACKAGED not found in src/ceap/paths.py")


class BuildPyWithData(build_py):
    def run(self) -> None:
        super().run()
        source = ROOT / "data"
        if not source.is_dir():  # building from an sdist made without data/: fail, never ship empty
            raise RuntimeError(f"{source} missing: the wheel would ship without its runtime data")
        target = Path(self.build_lib) / "ceap" / "_data"
        copied = 0
        for pattern in _packaged_globs():
            for src in sorted(source.glob(pattern)):
                dest = target / src.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                copied += 1
        if copied == 0:
            raise RuntimeError("no runtime data files matched ceap.paths.PACKAGED")
        self._vendor_external_knowledge(source / "reference" / "knowledge", target / "reference" / "knowledge")

    @staticmethod
    def _vendor_external_knowledge(source: Path, target: Path) -> None:
        """Copy corpus documents the manifest lists outside ``data/`` (``docs/quant/quant.md``) into
        ``knowledge/external/`` and point the packaged manifest there. The document's file name and
        metadata are unchanged, so the corpus hash is the same as in a checkout."""
        manifest = source / "manifest.json"
        if not manifest.exists():
            return
        spec = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in spec.get("external", []):
            src = (source / entry["path"]).resolve()
            if not src.exists():
                raise RuntimeError(f"knowledge manifest source missing at build time: {src}")
            dest = target / "external" / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            entry["path"] = f"external/{src.name}"
        (target / "manifest.json").write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")


setup(cmdclass={"build_py": BuildPyWithData})
