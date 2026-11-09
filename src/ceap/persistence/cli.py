"""``ceap db ...`` and ``ceap replay ...`` (R4 items 4.1 and 4.7), registered by ``ceap.cli``."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from ceap.persistence.db import LATEST_VERSION, MEMORY, Database
from ceap.persistence.repositories import RetentionPolicy, apply_retention

DEFAULT_DB = "var/ceap.db"


def _db_path(args: argparse.Namespace) -> str:
    path = args.path or os.getenv("CEAP_DB_PATH") or DEFAULT_DB
    if path == MEMORY:
        raise SystemExit("ceap db needs a database file; CEAP_DB_PATH is :memory: (set --path)")
    return path


def _print(doc: dict[str, Any]) -> None:
    print(json.dumps(doc, indent=2, default=str))


def _cmd_db(args: argparse.Namespace) -> int:
    path = _db_path(args)
    action = args.db_command
    if action == "migrate":
        with Database(path, migrate=False) as db:
            before = db.schema_version()
            applied = db.migrate(args.to)
            _print(
                {
                    "path": path,
                    "from": before,
                    "to": db.schema_version(),
                    "applied": applied,
                    "backup": str(db.last_backup) if db.last_backup else None,
                }
            )
        return 0
    if not os.path.exists(path):
        print(f"no database at {path}", file=sys.stderr)
        return 1
    with Database(path, migrate=False) as db:
        if action == "status":
            report = db.verify_chain()
            _print(
                {
                    "path": path,
                    "schema_version": db.schema_version(),
                    "latest": LATEST_VERSION,
                    "size_bytes": db.size_bytes(),
                    "audit_rows": report.rows,
                    "chain_ok": report.ok,
                    "dev_key": report.dev_key,
                }
            )
            return 0
        if action == "backup":
            dest = db.backup(args.dest)
            with Database(dest, migrate=False) as copy:
                ok = copy.verify_chain().ok
            _print({"backup": str(dest), "schema_version": db.schema_version(), "chain_ok": ok})
            return 0 if ok else 1
        if action == "verify-chain":
            if args.anchor:
                from ceap.persistence.anchor import load_anchor, verify_against_anchor

                result = verify_against_anchor(db, load_anchor(args.anchor))
                _print({"anchor": args.anchor, **result})
                return 0 if result["ok"] else 1
            report = db.verify_chain()
            _print(
                {
                    "ok": report.ok,
                    "rows": report.rows,
                    "first_bad_seq": report.first_bad_seq,
                    "reason": report.reason,
                    "head": report.head,
                    "dev_key": report.dev_key,
                }
            )
            return 0 if report.ok else 1
        if action == "anchor":
            from ceap.persistence.anchor import write_anchor

            _print(write_anchor(db, args.out))
            return 0
        if action == "kill-switch":
            from dataclasses import asdict

            from ceap.persistence.killswitch import KillSwitch

            ks = KillSwitch(db)
            if args.ks_action == "set":
                _print(asdict(ks.set(args.actor, args.reason or "")))
            elif args.ks_action == "clear":
                _print(asdict(ks.clear(args.actor, args.reason or "cleared")))
            else:
                _print({**asdict(ks.state()), "history": ks.history()[-10:]})
            return 0
        if action == "prune":
            from ceap.config import load_settings

            s = load_settings()
            policy = RetentionPolicy(
                s.retention_results_days or None,
                s.retention_traces_days or None,
                s.retention_llm_io_days or None,
                s.db_max_bytes,
            )
            _print({"pruned": apply_retention(db, policy), "size_bytes": db.size_bytes()})
            return 0
    raise SystemExit(f"unknown db command {action}")


def _cmd_replay(args: argparse.Namespace) -> int:
    from ceap.harness.replay import load_bundle, record_bundle, replay_bundle, save_bundle

    if args.record:
        from ceap.config import Settings
        from ceap.llm.client import MockLLMClient
        from ceap.platform import Platform, default_request, default_research_request

        settings = Settings(llm_provider="mock", auto_approve=True, db_path=MEMORY)
        platform = Platform(settings=settings, llm=MockLLMClient())
        dataset = args.dataset.upper()
        req = (
            default_research_request(dataset) if dataset.startswith("R") else default_request(dataset=dataset)
        )
        try:
            bundle = asyncio.run(record_bundle(platform, req))
        finally:
            platform.close()
        save_bundle(bundle, args.bundle)
        _print(
            {
                "bundle": args.bundle,
                "result_hash": bundle["result_hash"],
                "live": bundle["live"],
                "tool_calls": len(bundle["tool_calls"]),
                "model_calls": len(bundle["llm"]),
            }
        )
        return 0
    report = asyncio.run(replay_bundle(load_bundle(args.bundle)))
    _print(
        {
            "equal": report.equal,
            "stored_hash": report.stored_hash,
            "replayed_hash": report.replayed_hash,
            "first_difference": report.first_difference,
        }
    )
    return 0 if report.equal else 1


def register(sub: Any) -> None:
    db = sub.add_parser("db", help="SQLite persistence: status, migrate, backup, verify-chain, anchor, kill-switch, prune")
    db.add_argument("--path", default=None, help=f"database file (default: $CEAP_DB_PATH or {DEFAULT_DB})")
    dbs = db.add_subparsers(dest="db_command", required=True)
    dbs.add_parser("status", help="schema version, size, audit chain")
    mig = dbs.add_parser("migrate", help="apply forward migrations (backs the file up first)")
    mig.add_argument("--to", type=int, default=None, help="target schema version (default: latest)")
    bk = dbs.add_parser("backup", help="online backup to DEST, then verify the copy")
    bk.add_argument("dest")
    vc = dbs.add_parser("verify-chain", help="verify the audit log hash chain (exit 1 when broken)")
    vc.add_argument("--anchor", default=None, help="also check every chain against this anchor file")
    an = dbs.add_parser("anchor", help="write the chain heads to a git-committable anchor file (+ .sha256)")
    an.add_argument("--out", default="var/audit_head.json", help="anchor file (default var/audit_head.json)")
    ks = dbs.add_parser("kill-switch", help="show, set or clear the DB-backed kill switch (audited)")
    ks.add_argument("ks_action", choices=["status", "set", "clear"])
    ks.add_argument("--reason", default=None, help="why (required for set)")
    ks.add_argument("--actor", default=os.getenv("USERNAME") or os.getenv("USER") or "operator")
    dbs.add_parser("prune", help="apply the retention settings and the size bound")
    db.set_defaults(func=_cmd_db)

    rp = sub.add_parser("replay", help="replay a task bundle and check the result byte for byte")
    rp.add_argument("bundle", help="bundle file (read, or written with --record)")
    rp.add_argument(
        "--record", action="store_true", help="run a task offline (mock model) and write its bundle"
    )
    rp.add_argument(
        "--dataset", default="T01", help="--record: T*/S* investigation or R*/RS* research dataset"
    )
    rp.set_defaults(func=_cmd_replay)
