"""Keys and digests for the hash chains (audit log, paper ledger, paper days), 0.8.0 item A6.

Before 0.8.0 each chain link was ``sha256(prev_hash || canonical_json(row))``. Anyone with write access
to the SQLite file could edit a row and recompute every later hash, and ``verify_chain`` would still
say ok. From 0.8.0 each new link is an HMAC-SHA256 keyed with a secret that lives outside the
database, so recomputing the chain needs the key as well as the file.

Key source, in order:

* ``CEAP_AUDIT_HMAC_KEY``: the key (any string; use 32+ random bytes, e.g. ``python -c "import
  secrets; print(secrets.token_hex(32))"``). Keep it in the environment or a secret store, never in
  the database, the repository or a log line.
* otherwise :data:`DEV_DEFAULT_KEY`, a published development key. It is **not a secret**: a chain
  keyed with it is only as strong as the old plain SHA-256 chain. ``ceap db status`` and ``/health``
  report ``dev_key: true`` so it cannot be mistaken for a production setting.

Rotation: set the new key in ``CEAP_AUDIT_HMAC_KEY`` and list earlier keys, comma separated, in
``CEAP_AUDIT_HMAC_OLD_KEYS`` (the literal ``dev-default`` stands for :data:`DEV_DEFAULT_KEY`). Each row
records ``hash_alg = "hmac-sha256:<key id>"``; the key id is a one-way tag of the key, not the key.

Rows written before migration 0004 keep ``hash_alg = "sha256"`` and verify as before. They are
accepted only as a prefix of their chain whose length was counted when the migration ran
(``chain_epoch``); that count is sealed with an HMAC, so a rewritten chain cannot pass itself off as
legacy rows. External anchoring (:mod:`ceap.persistence.anchor`) covers what a key cannot: someone who
holds the key, or the dev default, can still rewrite history, but not without changing the head
recorded outside the database.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

KEY_ENV = "CEAP_AUDIT_HMAC_KEY"
OLD_KEYS_ENV = "CEAP_AUDIT_HMAC_OLD_KEYS"
# DEVELOPMENT ONLY. Published in the source, so it protects nothing; it exists so tests, the demo and
# the public paper forward log run without configuration. Set CEAP_AUDIT_HMAC_KEY for real use.
DEV_DEFAULT_KEY = b"ceap-DEV-ONLY-audit-hmac-key-NOT-A-SECRET"
DEV_DEFAULT_TOKEN = "dev-default"
LEGACY_ALG = "sha256"
HMAC_PREFIX = "hmac-sha256:"


def key_id(key: bytes) -> str:
    """Short one-way tag that names a key in ``hash_alg`` without revealing it."""
    return hashlib.sha256(b"ceap-chain-key-id\x00" + key).hexdigest()[:12]


def _decode(raw: str) -> bytes:
    raw = raw.strip()
    return DEV_DEFAULT_KEY if raw == DEV_DEFAULT_TOKEN else raw.encode("utf-8")


@dataclass(frozen=True)
class ChainKeys:
    """The current signing key plus every key accepted when verifying."""

    current: bytes
    accepted: dict[str, bytes]

    @staticmethod
    def from_env(key: bytes | str | None = None) -> ChainKeys:
        if key is None:
            raw = os.getenv(KEY_ENV, "")
            current = _decode(raw) if raw.strip() else DEV_DEFAULT_KEY
        else:
            current = key.encode("utf-8") if isinstance(key, str) else key
        accepted = {key_id(current): current}
        for item in os.getenv(OLD_KEYS_ENV, "").split(","):
            if item.strip():
                old = _decode(item)
                accepted[key_id(old)] = old
        return ChainKeys(current, accepted)

    @property
    def is_dev_default(self) -> bool:
        return self.current == DEV_DEFAULT_KEY

    @property
    def current_id(self) -> str:
        return key_id(self.current)

    @property
    def alg(self) -> str:
        return HMAC_PREFIX + self.current_id

    def sign(self, prev_hash: str, body: bytes) -> str:
        return hmac.new(self.current, prev_hash.encode("ascii") + body, hashlib.sha256).hexdigest()

    def digest(self, alg: str, prev_hash: str, body: bytes) -> str | None:
        """Recompute a link written with ``alg``; ``None`` when the key is not among the accepted ones."""
        if alg == LEGACY_ALG:
            return hashlib.sha256(prev_hash.encode("ascii") + body).hexdigest()
        if not alg.startswith(HMAC_PREFIX):
            return None
        key = self.accepted.get(alg[len(HMAC_PREFIX):])
        if key is None:
            return None
        return hmac.new(key, prev_hash.encode("ascii") + body, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------ chain epoch seal
    def seal_epoch(self, chain: str, legacy_rows: int) -> str:
        msg = f"ceap-chain-epoch\x00{chain}\x00{int(legacy_rows)}".encode()
        return f"{self.current_id}:{hmac.new(self.current, msg, hashlib.sha256).hexdigest()}"

    def epoch_ok(self, chain: str, legacy_rows: int, mac: str | None) -> bool:
        if not mac or ":" not in mac:
            return False
        kid, _, tag = mac.partition(":")
        key = self.accepted.get(kid)
        if key is None:
            return False
        msg = f"ceap-chain-epoch\x00{chain}\x00{int(legacy_rows)}".encode()
        return hmac.compare_digest(tag, hmac.new(key, msg, hashlib.sha256).hexdigest())


def walk_chain(
    rows: list[tuple[str, str, str, str, bytes]],
    keys: ChainKeys,
    legacy_rows: int,
) -> tuple[int | None, str]:
    """Walk ``(label, prev_hash, hash, hash_alg, body)`` rows in chain order.

    Returns ``(index of the first bad row, reason)``, or ``(None, "")`` when the chain is intact.
    """
    from ceap.persistence.db import GENESIS_HASH

    prev = GENESIS_HASH
    for n, (_label, prev_hash, digest, alg, body) in enumerate(rows):
        if prev_hash != prev:
            return n, "prev_hash does not match the previous row"
        if alg == LEGACY_ALG and n >= legacy_rows:
            return n, "unkeyed sha256 row after the legacy prefix (rows must be HMAC-keyed from 0.8.0)"
        expected = keys.digest(alg, prev, body)
        if expected is None:
            return n, f"row is keyed with an unknown key ({alg}); set {KEY_ENV} / {OLD_KEYS_ENV}"
        if not hmac.compare_digest(expected, digest):
            return n, "row content does not match its hash"
        prev = digest
    return None, ""
