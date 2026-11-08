"""``ceap paper ...``: run-day, backfill, report, export, verify (RP). Registered by ``ceap.cli``.

Exit codes: 0 done (or no-op), 2 refused (bad date, gap, paper-auto not allowed), 3 the date is not an
NYSE session (weekend or holiday: the scheduled workflow treats this as nothing to do), 4 end-of-day
reconciliation found breaks (nothing committed; the break report is printed and written).
The kill switch is the environment variable CEAP_PAPER_HALT (its value is the reason).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from ceap.paper.config import load_config
from ceap.paper.feed import LABEL
from ceap.paper.ledger import FORWARD_BOOK

EXIT_REFUSED = 2
EXIT_NO_SESSION = 3
EXIT_RECONCILIATION = 4


def _job(args: argparse.Namespace) -> Any:
    from ceap.paper.job import PaperJob
    from ceap.persistence.db import Database

    cfg = load_config(args.config)
    db = Database(args.db or cfg.db_path)
    return PaperJob(db, cfg, allow_paper_auto=getattr(args, "allow_paper_auto", False),
                    report_dir=getattr(args, "report_dir", None)), db


def _cmd_paper(args: argparse.Namespace) -> int:
    from ceap.paper.forward import (
        build_report,
        export_csv,
        format_report,
        in_sample_sharpe_from_backtest,
        verify_csv,
    )
    from ceap.paper.job import (
        NotASessionError,
        PaperAutoRefusedError,
        PaperJobError,
        PaperReconciliationError,
    )

    if args.paper_command == "verify-csv":
        rep_csv = verify_csv(args.csv)
        print(json.dumps(asdict(rep_csv)))
        return 0 if rep_csv.ok else 1
    job, db = _job(args)
    try:
        if args.paper_command == "run-day":
            outcomes = job.run_day(args.date)
        elif args.paper_command == "backfill":
            outcomes = job.backfill(args.date_from, args.date_to, args.book)
        elif args.paper_command == "report":
            cfg = job.config
            is_sr = cfg.in_sample_sharpe
            if is_sr is None and cfg.feed == "synthetic":
                is_sr = in_sample_sharpe_from_backtest(cfg.scenario, cfg.signal, cfg.rebalance, cfg.long_short,
                                                       cfg.gross_notional)
            rep = build_report(job.ledger, args.book, is_sr, job.rf_source, cfg.forward_start)
            print(json.dumps(rep, indent=2, default=str) if args.json else format_report(rep))
            return 0
        elif args.paper_command == "export":
            path = export_csv(job.ledger, args.book, args.out)
            print(f"wrote {path} ({LABEL})")
            return 0
        else:  # verify
            rep_v = job.ledger.verify(args.book)
            rec = job.ledger.reconcile(args.book)
            print(json.dumps({"chain": asdict(rep_v), "reconciliation": rec}, default=str))
            return 0 if rep_v.ok and rec["ok"] else 1
    except NotASessionError as exc:
        print(f"{exc}; nothing to do ({LABEL})")
        return EXIT_NO_SESSION
    except PaperReconciliationError as exc:
        print(f"RECONCILIATION FAILED: {exc}", file=sys.stderr)
        print(json.dumps(exc.report, indent=2, default=str), file=sys.stderr)
        return EXIT_RECONCILIATION
    except (PaperJobError, PaperAutoRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        db.close()
    for o in outcomes:
        eq = o.snapshot.get("equity")
        extra = f" equity={eq:.2f}" if isinstance(eq, float) else ""
        print(f"{o.book} {o.date} {o.status}{' (catch-up)' if o.catch_up else ''} orders={o.orders_staged}{extra}")
    print(f"label: {LABEL}")
    return 0


def register(sub: Any) -> None:
    pp = sub.add_parser("paper", help=f"paper trading: simulated fills and a forward record ({LABEL})")
    psub = pp.add_subparsers(dest="paper_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=None, help="SQLite file (default: config db_path)")
        p.add_argument("--config", default=None, help="config JSON (default: config/paper.json)")

    rd = psub.add_parser("run-day", help="process sessions up to --date in the forward book (idempotent)")
    rd.add_argument("--date", required=True)
    rd.add_argument("--allow-paper-auto", action="store_true",
                    help="let the paper-auto principal decide outside CEAP_ENV=dev (audited)")
    rd.add_argument("--report-dir", default=None, help="per-run markdown/JSON artefacts (default: config)")
    common(rd)
    bf = psub.add_parser("backfill", help="simulate a date range into a simulated book (never the forward record)")
    bf.add_argument("--from", dest="date_from", required=True)
    bf.add_argument("--to", dest="date_to", required=True)
    bf.add_argument("--book", default="simulated")
    bf.add_argument("--allow-paper-auto", action="store_true")
    common(bf)
    rp = psub.add_parser("report", help="forward Sharpe with 95 %% CI against the in-sample Sharpe")
    rp.add_argument("--book", default=FORWARD_BOOK)
    rp.add_argument("--json", action="store_true")
    common(rp)
    ex = psub.add_parser("export", help="export a book's log to CSV with schema_version and its hash chain")
    ex.add_argument("--book", default=FORWARD_BOOK)
    ex.add_argument("--out", required=True)
    common(ex)
    vf = psub.add_parser("verify", help="verify a book's hash chains and reconcile positions and cash")
    vf.add_argument("--book", default=FORWARD_BOOK)
    common(vf)
    vc = psub.add_parser("verify-csv", help="verify an exported CSV's hash chain")
    vc.add_argument("--csv", required=True)
    pp.set_defaults(func=_cmd_paper)
