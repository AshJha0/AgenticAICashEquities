"""Print a ``CEAP_API_KEY_HASHES`` entry for the key in the ``KEY`` environment variable (R-26).

Standard library only, so it runs without installing the package; the format matches
``ceap.api.auth.hash_api_key`` (a test checks the two agree). The key is read from the environment,
never from the command line, so it does not land in shell history or ``ps`` output.

    KEY=... python scripts/hash_api_key.py --role auditor [--id NAME] [--expires YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys


def entry(key: str, role: str, key_id: str | None = None, expires: str | None = None, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    out = f"sha256${salt.hex()}${hmac.new(salt, key.encode(), hashlib.sha256).hexdigest()}:{role}"
    if expires:
        out += f":{expires}"
    if key_id:
        out += f":id={key_id}"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--role", required=True)
    ap.add_argument("--id", dest="key_id")
    ap.add_argument("--expires")
    args = ap.parse_args(argv)
    key = os.environ.get("KEY", "")
    if not key:
        print("set KEY in the environment", file=sys.stderr)
        return 2
    print(entry(key, args.role, args.key_id, args.expires))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
