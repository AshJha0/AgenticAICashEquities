"""Write the API's OpenAPI document to ``docs/api/openapi.json`` (H15, I2; 0.9.0).

The file is checked in so a route or schema change shows up as a diff in review. CI's ``contracts``
job runs ``python scripts/export_openapi.py --check`` and fails when the committed file differs
from what the code generates; run without ``--check`` to regenerate.

Usage: python scripts/export_openapi.py [--check] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "api" / "openapi.json"


def render() -> str:
    os.environ.setdefault("ANTHROPIC_API_KEY", "")
    sys.path.insert(0, str(ROOT / "src"))
    from ceap.api.app import create_app

    spec = create_app().openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale; run: python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print(f"{args.out} matches the code")
        return 0
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
