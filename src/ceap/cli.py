"""Command-line interface: ``ceap investigate|scenarios|evaluate|generate-data|serve``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ceap.api.parsing import parse_question, resolve_session_date
from ceap.data.repositories import DatasetStore
from ceap.data.scenarios import SCENARIO_TEMPLATES, all_scenarios, get_scenario
from ceap.platform import InvestigationRequest, Platform


def _cmd_investigate(args: argparse.Namespace) -> int:
    parsed = parse_question(args.question, resolve_session_date(args.session_date))
    symbol = (args.symbol or parsed.symbol or "").upper()
    if not symbol or not parsed.window_start or not parsed.window_end:
        print(
            "Could not determine symbol and window from the question; e.g. 'Analyse AAPL execution between 14:00 and 15:00'",
            file=sys.stderr,
        )
        return 2
    req = InvestigationRequest(
        question=args.question,
        symbol=symbol,
        window_start=parsed.window_start,
        window_end=parsed.window_end,
        dataset=args.dataset,
        principal="cli",
        roles=frozenset({args.role}),
    )
    platform = Platform()
    try:
        result = asyncio.run(platform.investigate(req))
    except ValueError as exc:
        print(f"Cannot run investigation: {exc}", file=sys.stderr)
        return 2
    if args.json:
        Path(args.json).write_text(
            json.dumps(result.to_dict(include_trace=args.trace), indent=2, default=str)
        )
        print(f"wrote {args.json}")
    if result.report:
        print(result.report.narrative)
    else:
        print(f"Investigation {result.state.value}: {result.error}", file=sys.stderr)
        return 1
    print(
        f"\n[{result.state.value} in {result.duration_ms:.0f} ms; {len(result.findings)} findings; {len(result.evidence)} evidence records; warnings: {len(result.warnings)}]"
    )
    return 0


def _cmd_scenarios(args: argparse.Namespace) -> int:
    specs = all_scenarios() if args.all else list(SCENARIO_TEMPLATES)
    for s in specs:
        print(
            f"{s.id:4s} {s.symbol:6s} {s.template:26s} truth={[c.value for c in s.ground_truth]}  {s.description}"
        )
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from ceap.evaluation import run_evaluation

    summary = asyncio.run(run_evaluation(limit=args.limit, verbose=True))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    return 0 if summary["primary_accuracy"] >= 0.9 else 1


def _cmd_generate(args: argparse.Namespace) -> int:
    from ceap.data.export import export_dataset

    ds = DatasetStore().get(get_scenario(args.scenario))
    written = export_dataset(ds, Path(args.out), fmt=args.format)
    for p in written:
        print(p)
    print(json.dumps(ds.summary()))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("ceap.api.app:app", host=args.host, port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ceap", description="Cash Equities Agentic Platform")
    sub = p.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("investigate", help="run an execution-quality investigation")
    inv.add_argument("question")
    inv.add_argument("--symbol")
    inv.add_argument("--dataset", default="T01", help="scenario dataset id (T01..T10 or S01..S50)")
    inv.add_argument("--session-date", dest="session_date")
    inv.add_argument("--role", default="trader")
    inv.add_argument("--json", help="write the full result to this file")
    inv.add_argument("--trace", action="store_true", help="include the execution trace in --json output")
    inv.set_defaults(func=_cmd_investigate)

    sc = sub.add_parser("scenarios", help="list evaluation scenarios")
    sc.add_argument("--all", action="store_true", help="list all 50 concrete scenarios")
    sc.set_defaults(func=_cmd_scenarios)

    ev = sub.add_parser("evaluate", help="run the scenario evaluation suite")
    ev.add_argument("--limit", type=int, default=None)
    ev.set_defaults(func=_cmd_evaluate)

    gen = sub.add_parser("generate-data", help="export a synthetic dataset to CSV/Parquet")
    gen.add_argument("--scenario", default="T01")
    gen.add_argument("--out", default="data")
    gen.add_argument("--format", choices=("csv", "parquet"), default="csv")
    gen.set_defaults(func=_cmd_generate)

    sv = sub.add_parser("serve", help="run the FastAPI gateway")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
