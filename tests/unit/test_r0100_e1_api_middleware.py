"""Request middleware, start-up prewarm, rate-limit edges, key-hash parsing and request parsing
(0.10.0 E1 coverage for ``ceap.api``). The middleware is driven as raw ASGI so the body limit and
request-id rules are checked byte for byte."""

from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ceap.api import app as app_module
from ceap.api import ratelimit
from ceap.api.auth import KeyRecord, _lookup, hash_api_key, parse_key_hashes, parse_subjects
from ceap.api.middleware import DEFAULT_MAX_BODY_BYTES, RequestContextMiddleware
from ceap.api.models import ApprovalDecisionIn
from ceap.api.parsing import parse_question, parse_research_question, resolve_session_date
from ceap.config import SettingsError
from ceap.observability.metrics import metrics

# ------------------------------------------------------------------ middleware


def _scope(headers=(), limit: int | None = None, route_path: str | None = None) -> dict:
    scope = {"type": "http", "method": "POST", "headers": list(headers)}
    if limit is not None:
        scope["app"] = SimpleNamespace(
            state=SimpleNamespace(platform=SimpleNamespace(settings=SimpleNamespace(max_body_bytes=limit)))
        )
    if route_path is not None:
        scope["route"] = SimpleNamespace(path=route_path)
    return scope


async def _drive(app, scope, chunks=(b"",)):
    sent: list[dict] = []
    pending = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)
    ]

    async def receive():
        return pending.pop(0)

    async def send(message):
        sent.append(message)

    await RequestContextMiddleware(app)(scope, receive, send)
    return sent


def _echo_app(seen: list):
    async def app(scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        seen.append(body)
        await send(
            {"type": "http.response.start", "status": 201, "headers": [(b"content-type", b"text/plain")]}
        )
        await send({"type": "http.response.body", "body": body})

    return app


def _header(message: dict, name: bytes) -> bytes | None:
    return dict(message["headers"]).get(name)


async def test_safe_request_id_is_kept_and_unsafe_one_replaced():
    seen: list = []
    sent = await _drive(_echo_app(seen), _scope([(b"x-request-id", b"abc-123.Z_9")]), [b"hi"])
    assert sent[0]["status"] == 201 and _header(sent[0], b"x-request-id") == b"abc-123.Z_9"
    assert seen == [b"hi"]
    sent = await _drive(_echo_app([]), _scope([(b"x-request-id", b"bad id\r\nx: y")]))
    rid = _header(sent[0], b"x-request-id")
    assert rid != b"bad id\r\nx: y" and len(rid) == 32 and all(c in b"0123456789abcdef" for c in rid)


async def test_declared_oversize_body_is_refused_before_the_app_runs():
    seen: list = []
    sent = await _drive(_echo_app(seen), _scope([(b"content-length", b"11")], limit=10, route_path="/r413"))
    assert seen == [] and sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"detail": "request body exceeds 10 bytes"}'
    assert _header(sent[0], b"content-length") == str(len(sent[1]["body"])).encode()
    snap = metrics.snapshot()
    assert any(
        'route="/r413"' in k and 'status="413"' in k for k in snap if k.startswith("ceap_http_requests_total")
    )
    assert any('route="/r413"' in k and "4xx" in k for k in snap if k.startswith("ceap_http_errors_total"))


async def test_streamed_oversize_body_is_refused_once():
    seen: list = []
    sent = await _drive(_echo_app(seen), _scope(limit=4), [b"abc", b"de"])
    assert seen == [] and [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 413 and b"exceeds 4 bytes" in sent[1]["body"]


async def test_oversize_after_the_response_started_sends_nothing_more():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()
        await receive()  # second chunk crosses the limit

    sent = await _drive(app, _scope(limit=4), [b"abc", b"de"])
    assert [m["type"] for m in sent] == ["http.response.start"] and sent[0]["status"] == 200


async def test_non_http_scopes_pass_through_and_default_limit_applies():
    calls: list = []

    async def app(scope, receive, send):
        calls.append(scope["type"])

    await RequestContextMiddleware(app)({"type": "lifespan"}, None, None)
    assert calls == ["lifespan"]
    assert RequestContextMiddleware._max_body({}) == DEFAULT_MAX_BODY_BYTES
    assert RequestContextMiddleware._max_body(_scope(limit=123)) == 123


# ------------------------------------------------------------------ start-up prewarm


def test_prewarm_list_ignores_blanks(monkeypatch):
    monkeypatch.setenv(app_module.PREWARM_ENV, " T01, ,R01 ,")
    assert app_module._prewarm_datasets() == ["T01", "R01"]
    monkeypatch.delenv(app_module.PREWARM_ENV)
    assert app_module._prewarm_datasets() == []


async def test_prewarm_routes_research_ids_to_history_and_skips_unknown(caplog):
    got: list[tuple[str, str]] = []

    def history_get(sid):
        got.append(("history", sid))

    async def store_aget(sid):
        if sid == "T99":
            raise KeyError(sid)
        got.append(("store", sid))

    p = SimpleNamespace(history=SimpleNamespace(get=history_get), store=SimpleNamespace(aget=store_aget))
    with caplog.at_level(logging.INFO, logger="ceap.api.app"):
        await app_module._prewarm(p, ["r01", "T99", "T01"])
    assert got == [("history", "r01"), ("store", "T01")]  # the unknown id did not stop the rest
    assert "unknown dataset T99, skipped" in caplog.text


# ------------------------------------------------------------------ rate limit edges


def test_full_buckets_are_evicted_first_when_the_table_is_full():
    now = [0.0]
    rl = ratelimit.RateLimiter(60, 2, clock=lambda: now[0], max_principals=2)
    rl.acquire("busy")  # 1 token left: not full, must survive
    rl.acquire("busy")
    now[0] += 0.0
    rl._buckets["idle"] = ratelimit._Bucket(2.0, now[0])  # full bucket
    assert rl.acquire("new") == 0.0
    assert set(rl._buckets) == {"busy", "new"}
    assert rl.acquire("busy") > 0  # busy kept its empty bucket rather than getting a fresh one


@pytest.mark.parametrize(("per_minute", "burst"), [("abc", None), ("10", "x"), ("10", "0")])
def test_rate_limit_env_rejects_non_numbers_and_zero_burst(monkeypatch, per_minute, burst):
    monkeypatch.setenv(ratelimit.PER_MINUTE_ENV, per_minute)
    if burst is None:
        monkeypatch.delenv(ratelimit.BURST_ENV, raising=False)
    else:
        monkeypatch.setenv(ratelimit.BURST_ENV, burst)
    with pytest.raises(SettingsError, match=ratelimit.PER_MINUTE_ENV):
        ratelimit.from_env()


def test_rate_limit_env_defaults(monkeypatch):
    monkeypatch.delenv(ratelimit.PER_MINUTE_ENV, raising=False)
    monkeypatch.delenv(ratelimit.BURST_ENV, raising=False)
    rl = ratelimit.from_env()
    assert rl is not None and rl.burst == ratelimit.DEFAULT_BURST and rl.rate == pytest.approx(1.0)


# ------------------------------------------------------------------ key hashes


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ("sha256$aa$" + "0" * 64 + ":trader:id=has space", "key ids must be"),
        ("sha256$aa$" + "0" * 64, "entries must be"),
        ("sha256$aa$" + "0" * 64 + ":trader:2027-01-01:extra", "entries must be"),
        ("md5$aa$" + "0" * 64 + ":trader", "not a sha256"),
        ("sha256$aa$abc:trader", "not a sha256"),
        ("sha256$aa$" + "0" * 64 + ":wizard", "unknown role 'wizard'"),
        ("sha256$aa$" + "0" * 64 + ":trader:subject=bad subject", "subjects must be"),
    ],
)
def test_bad_key_hash_entries_are_refused(entry, match):
    with pytest.raises(SettingsError, match=match):
        parse_key_hashes(entry)


def test_key_hash_entry_with_expiry_id_and_subject():
    entry = hash_api_key("k", salt=b"\x01" * 16) + ":approver:2027-01-01:id=desk-1:subject=alice"
    (rec,) = parse_key_hashes(" , " + entry + " ,")
    assert (rec.role, rec.expires, rec.key_id, rec.subject) == (
        "approver",
        date(2027, 1, 1),
        "desk-1",
        "alice",
    )
    assert rec.matches("k") and not rec.matches("K")
    assert not rec.expired(date(2027, 1, 1)) and rec.expired(date(2027, 1, 2))
    assert "salt" not in repr(rec) and rec.digest not in repr(rec)


def test_lookup_checks_every_record_and_returns_the_last_match():
    a = parse_key_hashes(hash_api_key("same") + ":trader")[0]
    b = parse_key_hashes(hash_api_key("same") + ":viewer")[0]
    other = parse_key_hashes(hash_api_key("other") + ":admin")[0]
    assert _lookup([a, other, b], "same") is b
    assert _lookup([other], "same") is None
    assert isinstance(a, KeyRecord)


@pytest.mark.parametrize(
    "raw", ["trader:abcd1234", "Trader:abcd1234=alice", "trader:=alice", "trader:a b=alice"]
)
def test_bad_subject_mappings_are_refused(raw):
    with pytest.raises(SettingsError):
        parse_subjects(raw)


# ------------------------------------------------------------------ approval decision body


def test_approval_decision_action_must_agree_and_rejections_need_a_reason():
    assert ApprovalDecisionIn(approved=True).approved is True
    with pytest.raises(ValidationError, match="does not match"):
        ApprovalDecisionIn(approved=True, action="reject", comment="no")
    with pytest.raises(ValidationError, match="needs a reason"):
        ApprovalDecisionIn(approved=False, action="reject")
    ok = ApprovalDecisionIn(approved=False, action="reject", comment="spread too wide")
    assert ok.comment == "spread too wide"


# ------------------------------------------------------------------ question parsing edges


def test_parse_question_relative_days_am_pm_and_bad_dates():
    today = date(2026, 3, 3)
    p = parse_question("AAPL yesterday from 2pm to 3pm", date(2026, 1, 2), today=today)
    assert p.relative_day == "yesterday" and p.session_date == date(2026, 3, 2)
    assert (p.window_start.hour, p.window_end.hour) == (14, 15)
    p = parse_question("MSFT today between 12am and 1am", date(2026, 1, 2), today=today)
    assert p.relative_day == "today" and (p.window_start.hour, p.window_end.hour) == (0, 1)
    p = parse_question("AAPL on 2026-02-30 between 14:00 and 15:00", date(2026, 1, 2), today=today)
    assert p.session_date == date(2026, 1, 2)  # malformed date in prose: default kept, no 500
    p = parse_question("AAPL slippage from 3 to 5 bps", date(2026, 1, 2), today=today)
    assert p.window_start is None and p.window_end is None  # a numeric range with a unit is not a window
    p = parse_question("AAPL between 15:00 and 14:00", date(2026, 1, 2), today=today)
    assert (p.window_end - p.window_start).total_seconds() == 23 * 3600  # end before start rolls a day


def test_parse_research_question_keywords_dataset_and_bad_ranges():
    r = parse_research_question("Is there low-vol premium in rs03 from 2020-01-01 to 2021-06-30?")
    assert (r.signal, r.dataset, r.start, r.end) == (
        "low_vol_60",
        "RS03",
        date(2020, 1, 1),
        date(2021, 6, 30),
    )
    r = parse_research_question("mean reversion between 2021-02-30 and 2021-06-30")
    assert r.signal == "reversal_5" and r.start is None and r.end is None
    r = parse_research_question("momentum from 2021-06-30 to 2020-01-01")
    assert r.start is None and r.end is None  # an inverted range is dropped
    assert parse_research_question("what about the weather").signal is None


def test_resolve_session_date_explicit_and_default():
    from ceap.data.synthetic import DEFAULT_SESSION_DATE

    assert resolve_session_date("2026-03-02") == date(2026, 3, 2)
    assert resolve_session_date(None) == DEFAULT_SESSION_DATE
    with pytest.raises(ValueError):
        resolve_session_date("2026-02-30")
