"""Command-line interface: ``ceap investigate|research|scenarios|research-scenarios|evaluate|generate-data|serve``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from ceap.analytics.signals import SIGNALS
from ceap.api.parsing import parse_question, parse_research_question, resolve_session_date
from ceap.config import SettingsError
from ceap.data.repositories import DatasetStore
from ceap.data.research_scenarios import RESEARCH_TEMPLATES, all_research_scenarios
from ceap.data.scenarios import SCENARIO_TEMPLATES, all_scenarios, get_scenario
from ceap.domain.common import to_jsonable
from ceap.platform import (
    DEFAULT_RESEARCH_DATASET,
    InvestigationRequest,
    Platform,
    RequestNotPermitted,
    ResearchRequest,
)
from ceap.policy.permissions import Role


def _write_json(path: str, result, include_trace: bool) -> None:
    Path(path).write_text(
        json.dumps(to_jsonable(result.to_dict(include_trace=include_trace)), indent=2, default=str, allow_nan=False),
        encoding="utf-8",
    )
    print(f"wrote {path}")


def _print_outcome(result) -> int:
    if result.report:
        print(result.report.narrative)
    else:
        print(f"{result.state.value}: {result.error}", file=sys.stderr)
        return 1
    print(
        f"\n[{result.state.value} in {result.duration_ms:.0f} ms; {len(result.findings)} findings; {len(result.evidence)} evidence records; warnings: {len(result.warnings)}]"
    )
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    parsed = parse_research_question(args.question)
    signal = args.signal or parsed.signal or "momentum_12_1"
    dataset = (args.dataset or parsed.dataset or DEFAULT_RESEARCH_DATASET).upper()
    try:
        start = date.fromisoformat(args.start) if args.start else parsed.start
        end = date.fromisoformat(args.end) if args.end else parsed.end
        split = date.fromisoformat(args.in_sample_end) if args.in_sample_end else None
    except ValueError as exc:
        print(f"Invalid date: {exc}", file=sys.stderr)
        return 2
    rebalance = args.rebalance_days or (SIGNALS[signal].default_rebalance_days if signal in SIGNALS else 21)
    req = ResearchRequest(
        question=args.question,
        signal=signal,
        dataset=dataset,
        start=start,
        end=end,
        in_sample_end=split,
        rebalance_days=rebalance,
        long_short=not args.long_only,
        gross_notional=args.gross_notional,
        stage_orders=args.stage_orders,
        principal="cli",
        roles=frozenset({args.role}),
    )
    platform = Platform()
    try:
        result = asyncio.run(platform.research(req))
    except RequestNotPermitted as exc:
        print(f"Not permitted: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"Cannot run research: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _write_json(args.json, result, args.trace)
    return _print_outcome(result)


def _cmd_research_scenarios(args: argparse.Namespace) -> int:
    from ceap.data.ground_truth import derive_ground_truth

    specs = all_research_scenarios() if args.all else list(RESEARCH_TEMPLATES)
    for s in specs:
        flags = [f.value for f in s.expected_flags]
        truth = derive_ground_truth(s)
        near = " near-threshold" if truth.near_threshold else ""
        print(
            f"{s.id:4s} {s.signal:14s} {s.template:24s} verdict={s.expected_verdict.value:7s} flags={flags}{near}  "
            f"E[t]={truth.metrics['t_is']:.2f} E[net SR oos]={truth.metrics['net_sharpe_oos']:.2f}  {s.description}"
        )
    return 0


def _cmd_profitability(args: argparse.Namespace) -> int:
    from ceap.analytics.profitability import json_safe, profitability_table
    from ceap.data.historical import HistoricalStore

    ds = HistoricalStore().get(args.dataset)
    signal = args.signal or ds.scenario.signal
    table = profitability_table(
        ds, signal, rebalance_days=args.rebalance_days or ds.scenario.rebalance_days,
        long_short=not args.long_only, gross_notional=args.gross_notional,
    )
    print(json.dumps(json_safe(table), indent=2))
    return 0


def _cmd_investigate(args: argparse.Namespace) -> int:
    try:
        parsed = parse_question(args.question, resolve_session_date(args.session_date))
    except ValueError as exc:
        print(f"Invalid --session-date: {exc}", file=sys.stderr)
        return 2
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
            json.dumps(
                to_jsonable(result.to_dict(include_trace=args.trace)), indent=2, default=str, allow_nan=False
            ),
            encoding="utf-8",
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
    from ceap.evaluation import run_evaluation, run_research_evaluation

    ok = True
    if args.suite == "null":
        from ceap.analytics.null_sweep import run_null_sweep, write_thresholds

        sweep = run_null_sweep(args.seeds, workers=args.workers)
        chosen = sweep["chosen"]
        from ceap.analytics.null_sweep import false_promote_curve
        from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS

        stats = [p["stat"] if p["stat"] is not None else float("-inf") for p in sweep["per_seed"]]
        at_hurdle = next(r for r in false_promote_curve(stats) if r["hurdle"] >= DEFAULT_THRESHOLDS.ic_t_stat_min - 1e-9)
        print(json.dumps({
            "seeds": sweep["n_seeds"],
            "search_size": sweep["search_size"],
            "sweep_hash": sweep["sweep_hash"],
            "committed_hurdle": DEFAULT_THRESHOLDS.ic_t_stat_min,
            "committed_sweep_hash": DEFAULT_THRESHOLDS.hurdle_sweep_hash,
            "false_promote_rate_at_committed_hurdle": at_hurdle["rate"],
            "band95_at_committed_hurdle": [at_hurdle["lower"], at_hurdle["upper"]],
            "chosen_by_this_sweep": chosen,
            "abs_t_gt_2_rate": sweep["abs_t_gt_2_rate"],
        }, indent=2))
        if args.write_thresholds:
            print(f"wrote {write_thresholds(sweep)}")
        return 0
    if args.suite in ("investigation", "all"):
        summary = asyncio.run(run_evaluation(limit=args.limit, verbose=True))
        print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
        ok = ok and summary["primary_accuracy"] >= 0.9
    if args.suite in ("research", "all"):
        summary = asyncio.run(run_research_evaluation(limit=args.limit, verbose=True))
        print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
        ok = ok and summary["clear_verdict_accuracy"] >= 0.8
    return 0 if ok else 1


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
    inv.add_argument("--role", default="trader", choices=[r.value for r in Role])
    inv.add_argument("--json", help="write the full result to this file")
    inv.add_argument("--trace", action="store_true", help="include the execution trace in --json output")
    inv.set_defaults(func=_cmd_investigate)

    rs = sub.add_parser("research", help="evaluate a trading signal and propose whether to promote it (Stage 2)")
    rs.add_argument("question")
    rs.add_argument("--signal", choices=sorted(SIGNALS), help="signal id; parsed from the question when omitted")
    rs.add_argument("--dataset", help="research dataset id (R01..R10 or RS01..RS30); default R01")
    rs.add_argument("--start", help="YYYY-MM-DD (defaults to the dataset's research start)")
    rs.add_argument("--end", help="YYYY-MM-DD (defaults to the dataset's last day)")
    rs.add_argument("--in-sample-end", dest="in_sample_end", help="YYYY-MM-DD walk-forward split")
    rs.add_argument("--rebalance-days", dest="rebalance_days", type=int, help="defaults to the signal's")
    rs.add_argument("--long-only", dest="long_only", action="store_true", help="long-only instead of long-short")
    rs.add_argument("--gross-notional", dest="gross_notional", type=float, default=50_000_000.0)
    rs.add_argument(
        "--stage-orders", dest="stage_orders", action="store_true", help="stage paper orders after approval (admin)"
    )
    rs.add_argument("--role", default="quant", choices=[r.value for r in Role])
    rs.add_argument("--json", help="write the full result to this file")
    rs.add_argument("--trace", action="store_true", help="include the execution trace in --json output")
    rs.set_defaults(func=_cmd_research)

    sc = sub.add_parser("scenarios", help="list evaluation scenarios")
    sc.add_argument("--all", action="store_true", help="list all 50 concrete scenarios")
    sc.set_defaults(func=_cmd_scenarios)

    rsc = sub.add_parser("research-scenarios", help="list research evaluation scenarios")
    rsc.add_argument("--all", action="store_true", help="list all 30 concrete research scenarios")
    rsc.set_defaults(func=_cmd_research_scenarios)

    pf = sub.add_parser("profitability", help="profitability table with a capacity sweep for one signal (2.14)")
    pf.add_argument("--dataset", default="R01")
    pf.add_argument("--signal", choices=sorted(SIGNALS))
    pf.add_argument("--rebalance-days", dest="rebalance_days", type=int)
    pf.add_argument("--long-only", dest="long_only", action="store_true")
    pf.add_argument("--gross-notional", dest="gross_notional", type=float, default=50_000_000.0)
    pf.set_defaults(func=_cmd_profitability)

    ev = sub.add_parser("evaluate", help="run the scenario evaluation suite(s)")
    ev.add_argument("--limit", type=int, default=None)
    ev.add_argument("--suite", choices=("investigation", "research", "null", "all"), default="investigation")
    ev.add_argument("--seeds", type=int, default=50, help="null suite: pure-noise seeds (500 nightly)")
    ev.add_argument("--workers", type=int, default=1, help="null suite: worker processes")
    ev.add_argument(
        "--write-thresholds", dest="write_thresholds", action="store_true",
        help="null suite: write the chosen hurdle and sweep hash to data/reference/research_thresholds.json",
    )

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

    from ceap.persistence.cli import register as register_platform_commands

    register_platform_commands(sub)  # ceap db ..., ceap replay ... (R4)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (
        sys.stdout,
        sys.stderr,
    ):  # Windows consoles default to cp1252; reports may contain '→', '≈'
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
