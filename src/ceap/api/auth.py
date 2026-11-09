"""API-key authentication and capability-based authorisation.

Principals are identified by a stable, non-secret label derived from the
key (a truncated SHA-256), never by any part of the key itself, because
the principal name is written into approval requests, traces and the
cancel reason - places other principals can read.

Keys are held as salted HMAC-SHA256 digests (R4 item 4.6). Raw keys from
``CEAP_API_KEYS`` are hashed at start-up with a per-process random salt;
``CEAP_API_KEY_HASHES`` carries pre-hashed keys
(``sha256$<salt hex>$<digest hex>:<role>[:<expires YYYY-MM-DD>]``) so a
deployment need not hold raw keys at all. Produce an entry with
``python -c "from ceap.api.auth import hash_api_key; print(hash_api_key('KEY'))"``.
The raw key is never logged, echoed in an error, or kept in a repr.

Four-eyes binds to a person, not a key (A13). Map each key to a named subject, either with a
``subject=<name>`` field on a ``CEAP_API_KEY_HASHES`` entry
(``sha256$..$..:approver:2027-01-01:subject=alice``) or with ``CEAP_API_KEY_SUBJECTS``
(``<principal label>=<name>,...``, where the label is the key's ``role:xxxxxxxx`` form). A mapped
key's principal is ``user:<name>``, so the same person holding a trader key and an approver key
is one principal and cannot approve their own request. An unmapped key stays its own principal.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from ceap.config import Settings, SettingsError
from ceap.policy.permissions import Role, capabilities_for

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_UNAUTHORISED_HEADERS = {"WWW-Authenticate": "ApiKey"}


SUBJECTS_ENV = "CEAP_API_KEY_SUBJECTS"
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")


@dataclass(frozen=True)
class Principal:
    name: str
    roles: frozenset[str]
    subject: str | None = None

    @property
    def capabilities(self) -> frozenset[str]:
        return capabilities_for(self.roles)


@dataclass(frozen=True)
class KeyRecord:
    salt: bytes = field(repr=False)
    digest: str = field(repr=False)
    role: str
    expires: date | None = None
    subject: str | None = None

    def matches(self, presented: str) -> bool:
        candidate = hmac.new(self.salt, presented.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(candidate, self.digest)

    def expired(self, today: date | None = None) -> bool:
        return self.expires is not None and (today or datetime.now(UTC).date()) > self.expires


def hash_api_key(api_key: str, salt: bytes | None = None) -> str:
    """The ``sha256$<salt>$<digest>`` form for ``CEAP_API_KEY_HASHES`` (append ``:role[:expiry]``)."""
    salt = salt or os.urandom(16)
    return f"sha256${salt.hex()}${hmac.new(salt, api_key.encode(), hashlib.sha256).hexdigest()}"


def principal_label(role: str, api_key: str) -> str:
    return f"{role}:{hashlib.sha256(api_key.encode()).hexdigest()[:8]}"


def subject_name(subject: str) -> str:
    """The principal name of a named person (A13)."""
    return f"user:{subject}"


def _check_subject(subject: str) -> str:
    if not _SUBJECT_RE.match(subject):
        raise SettingsError("API key subjects must be 1-64 characters of letters, digits and ._@-")
    return subject


def parse_subjects(raw: str) -> dict[str, str]:
    """``CEAP_API_KEY_SUBJECTS``: ``<role:xxxxxxxx>=<subject>`` pairs, comma-separated."""
    out: dict[str, str] = {}
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        label, sep, subject = entry.partition("=")
        if not sep or not re.match(r"^[a-z]+:[0-9a-f]{8}$", label.strip()):
            raise SettingsError(f"{SUBJECTS_ENV} entries must be role:xxxxxxxx=subject")
        out[label.strip()] = _check_subject(subject.strip())
    return out


def parse_key_hashes(raw: str) -> list[KeyRecord]:
    records: list[KeyRecord] = []
    valid_roles = {r.value for r in Role}
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        parts = entry.split(":")
        subject = None
        if len(parts) >= 3 and parts[-1].startswith("subject="):
            subject = _check_subject(parts.pop()[len("subject=") :])
        if len(parts) not in (2, 3):
            raise SettingsError(
                "CEAP_API_KEY_HASHES entries must be sha256$salt$digest:role[:YYYY-MM-DD][:subject=NAME]"
            )
        algo, _, rest = parts[0].partition("$")
        salt_hex, _, digest = rest.partition("$")
        if algo != "sha256" or not salt_hex or len(digest) != 64:
            raise SettingsError("CEAP_API_KEY_HASHES entry is not a sha256$salt$digest value")
        if parts[1] not in valid_roles:
            raise SettingsError(f"unknown role {parts[1]!r} in CEAP_API_KEY_HASHES")
        expires = date.fromisoformat(parts[2]) if len(parts) == 3 else None
        records.append(KeyRecord(bytes.fromhex(salt_hex), digest, parts[1], expires, subject))
    return records


def build_key_records(settings: Settings) -> list[KeyRecord]:
    salt = os.urandom(16)
    records = [
        KeyRecord(salt, hmac.new(salt, key.encode(), hashlib.sha256).hexdigest(), role)
        for key, role in settings.api_keys.items()
    ]
    return records + parse_key_hashes(settings.api_key_hashes)


def _records(request: Request) -> list[KeyRecord]:
    settings = request.app.state.platform.settings
    cached = getattr(request.app.state, "key_records", None)
    if cached is None or cached[0] is not settings:
        cached = (settings, build_key_records(settings))
        request.app.state.key_records = cached
    return cached[1]


def _lookup(records: list[KeyRecord], presented: str) -> KeyRecord | None:
    """Checks every record (no early exit) so timing does not reveal which entry matched."""
    match: KeyRecord | None = None
    for record in records:
        if record.matches(presented):
            match = record
    return match


async def current_principal(request: Request, api_key: str | None = Depends(api_key_header)) -> Principal:
    if not api_key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header", headers=_UNAUTHORISED_HEADERS
        )
    record = _lookup(_records(request), api_key)
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key", headers=_UNAUTHORISED_HEADERS)
    if record.expired():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired", headers=_UNAUTHORISED_HEADERS)
    label = principal_label(record.role, api_key)
    subject = record.subject or _subjects(request).get(label)
    name = subject_name(subject) if subject else label
    return Principal(name=name, roles=frozenset({record.role}), subject=subject)


def _subjects(request: Request) -> dict[str, str]:
    raw = os.getenv(SUBJECTS_ENV) or ""
    cached = getattr(request.app.state, "key_subjects", None)
    if cached is None or cached[0] != raw:
        cached = (raw, parse_subjects(raw))
        request.app.state.key_subjects = cached
    return cached[1]


def require_capability(capability: str) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if capability not in principal.capabilities:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"role {sorted(principal.roles)} lacks capability {capability}"
            )
        return principal

    return dependency
