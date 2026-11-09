# COOKBOOK.md — Recipes

Short, copy-pasteable recipes for the things people actually do with the platform. Every
recipe runs offline with the deterministic model unless it says otherwise. Paths are relative
to the repository root; build and activate the virtual environment first (`scripts/bootstrap.sh`,
or `.\scripts\bootstrap.ps1` on Windows; recipe 34).

Contents:

1. [Run an investigation from the CLI](#1-run-an-investigation-from-the-cli)
2. [Run an investigation from Python](#2-run-an-investigation-from-python)
3. [Save the full result (plan, findings, evidence, trace) as JSON](#3-save-the-full-result-as-json)
4. [Pick a scenario and symbol](#4-pick-a-scenario-and-symbol)
5. [Use the API](#5-use-the-api)
6. [Turn on human approvals](#6-turn-on-human-approvals)
7. [Use a real model](#7-use-a-real-model)
8. [Call an MCP tool directly (in-process)](#8-call-an-mcp-tool-directly-in-process)
9. [Run the MCP servers over stdio](#9-run-the-mcp-servers-over-stdio)
10. [Compute TCA metrics without any agent](#10-compute-tca-metrics-without-any-agent)
11. [Run attribution on your own numbers](#11-run-attribution-on-your-own-numbers)
12. [Tune attribution thresholds](#12-tune-attribution-thresholds)
13. [Add a scenario template](#13-add-a-scenario-template)
14. [Export a dataset to CSV / Parquet (DuckDB, Polars)](#14-export-a-dataset-to-csv--parquet)
15. [Add a knowledge document](#15-add-a-knowledge-document)
16. [Add an MCP tool](#16-add-an-mcp-tool)
17. [Add a new MCP server backed by a real data source](#17-add-a-new-mcp-server-backed-by-a-real-data-source)
18. [Add a specialist agent](#18-add-a-specialist-agent)
19. [Restrict what a principal may do](#19-restrict-what-a-principal-may-do)
20. [Cancel a running investigation](#20-cancel-a-running-investigation)
21. [Read the trace and policy log](#21-read-the-trace-and-policy-log)
22. [Inspect the critic's assessment](#22-inspect-the-critics-assessment)
23. [Run the evaluation and read the table](#23-run-the-evaluation-and-read-the-table)
24. [Run only the fast tests](#24-run-only-the-fast-tests)
25. [Export traces to OpenTelemetry](#25-export-traces-to-opentelemetry)
26. [Run in Docker](#26-run-in-docker)
27. [Publish the docs site](#27-publish-the-docs-site)
28. [Run a signal research proposal (Stage 2)](#28-run-a-signal-research-proposal-stage-2)
29. [Run a research proposal from Python](#29-run-a-research-proposal-from-python)
30. [Stage paper orders behind a human approval (Stage 2)](#30-stage-paper-orders-behind-a-human-approval-stage-2)
31. [Use the research API](#31-use-the-research-api)
32. [Add a new signal](#32-add-a-new-signal)
33. [Add a research scenario template](#33-add-a-research-scenario-template)
34. [Day-to-day development: bootstrap, test tiers, lock, release](#34-day-to-day-development-bootstrap-test-tiers-lock-release)
35. [Measure the PROMOTE hurdle (null sweep)](#35-measure-the-promote-hurdle-null-sweep)
36. [Profitability table and capacity sweep](#36-profitability-table-and-capacity-sweep)
37. [Backtest under different execution assumptions](#37-backtest-under-different-execution-assumptions)
38. [Keep state across restarts (SQLite)](#38-keep-state-across-restarts-sqlite)
39. [Replay a task byte for byte](#39-replay-a-task-byte-for-byte)
40. [Scrape metrics and correlate logs](#40-scrape-metrics-and-correlate-logs)
41. [Hold API keys as hashes only](#41-hold-api-keys-as-hashes-only)

---

## 1. Run an investigation from the CLI

```bash
ceap investigate "Why did our execution quality for AAPL deteriorate between 14:00 and 15:00?" --dataset T06
```

The symbol and window are parsed from the question ("between 14:00 and 15:00", "from 2pm to
3:30pm"; naive times are America/New_York, the desk session clock since 0.5.0). `--dataset` picks the scenario (T01–T10 templates,
S01–S50 concrete). Add `--role quant|admin|viewer` to change the principal's role.

## 2. Run an investigation from Python

```python
import asyncio
from ceap.platform import Platform, default_request

platform = Platform()                       # mock LLM unless ANTHROPIC_API_KEY is set
result = asyncio.run(platform.investigate(default_request("AAPL", "T05")))

print(result.state, result.duration_ms)     # HarnessState.COMPLETED ~900
print(result.report.attribution["primary"]) # VENUE_DEGRADATION
print(result.report.narrative)
for f in result.findings:
    print(f.confidence, f.statement, f.supporting_evidence)
```

To build a request by hand:

```python
from datetime import datetime
from ceap.data.session import SESSION_TZ  # America/New_York
from ceap.platform import InvestigationRequest

req = InvestigationRequest(
    question="Analyse MSFT VWAP execution and identify the main sources of slippage.",
    symbol="MSFT",
    window_start=datetime(2026, 9, 18, 14, 0, tzinfo=SESSION_TZ),
    window_end=datetime(2026, 9, 18, 15, 0, tzinfo=SESSION_TZ),
    dataset="S12",                           # MSFT / wide_spreads
    principal="quant.b", roles=frozenset({"quant"}),
)
```

`platform.validate_request(req)` raises `ValueError` with a precise reason if the symbol,
window, baseline ordering or dataset coverage is wrong.

## 3. Save the full result as JSON

```bash
ceap investigate "Analyse AAPL execution between 14:00 and 15:00" --dataset T08 --json result.json --trace
```

`result.json` contains the plan, findings, evidence, report, state history, policy log, step
results, agent outputs and (with `--trace`) the span tree. In Python:
`result.to_dict(include_trace=True)`.

## 4. Pick a scenario and symbol

```bash
ceap scenarios          # the 10 templates
ceap scenarios --all    # all 50 (S01..S50) with symbol and ground truth
```

Each dataset covers one symbol; the request validator tells you if you mix them
(`dataset S07 covers MSFT, not AAPL`). Templates T01–T10 are AAPL.

## 5. Use the API

```bash
ceap serve                                   # http://127.0.0.1:8000/docs
```

```bash
# start (202 Accepted; returns task_id and links)
curl -s -X POST localhost:8000/investigations -H "X-API-Key: dev-trader-key" \
  -H "Content-Type: application/json" \
  -d '{"question":"Why did AAPL execution deteriorate between 14:00 and 15:00?","dataset":"T05"}'

# poll, then read
curl -s localhost:8000/investigations/TASK-xxxx           -H "X-API-Key: dev-trader-key"
curl -s localhost:8000/investigations/TASK-xxxx/report    -H "X-API-Key: dev-trader-key"
curl -s "localhost:8000/investigations/TASK-xxxx/result?include_trace=true" -H "X-API-Key: dev-trader-key"
curl -s localhost:8000/investigations/TASK-xxxx/trace     -H "X-API-Key: dev-auditor-key"
```

Keys → roles come from `CEAP_API_KEYS=key:role,...`. Reads are scoped to the owner (another
principal's task is a 404); `auditor`, `approver` and `admin` read everything. Trader, quant and
engineer can investigate; only `approver` decides approvals. Retry a POST safely with
`-H "Idempotency-Key: <your id>"` (same body: same `task_id`; different body: 422).

## 6. Turn on human approvals

```bash
CEAP_AUTO_APPROVE=false ceap serve
```

A MEDIUM-risk tool (`risk.calculate_stress`) called by a principal without `risk:medium`
(e.g. viewer) parks the investigation in `AWAITING_APPROVAL`:

```bash
curl -s localhost:8000/approvals -H "X-API-Key: dev-admin-key"
curl -s -X POST localhost:8000/approvals/APR-xxxx -H "X-API-Key: dev-admin-key" \
  -H "Content-Type: application/json" -d '{"approved": true, "comment": "ok for TCA review"}'
```

Unanswered approvals expire after `CEAP_APPROVAL_TIMEOUT_S` (120 s) and the step is marked DENIED;
the investigation continues without it. A decision posted after expiry, after a restart, or after
another approver already decided gets 409 and an audit row. While a task waits, its concurrency
slot is free for other work.

## 7. Use a real model

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=sk-ant-...
export CEAP_LLM_PROVIDER=anthropic
export CEAP_LLM_MODEL=claude-sonnet-5-5            # narrator/critic; must be pinned in src/ceap/llm/model_pins.toml
export CEAP_LLM_PLANNING_MODEL=claude-opus-5-5     # the pinned planning model
ceap investigate "..." --dataset T06
```

`CEAP_LLM_PROVIDER` is `mock` or `anthropic` (`auto` was removed in 0.4.0). The layer fails closed:
`anthropic` without a working client refuses to start instead of quietly using the mock, and a model
id that is not in `model_pins.toml` is refused. Any fallback (the pinned fallback model after a
refusal, a repair retry, a canonical plan, a templated narrative) is recorded as a warning and marks
the result `DEGRADED`. The planner's `rejected_steps` and the report's `narrative_number_warnings`
tell you how the model behaved. No result in this repository comes from a live model call: anything
you measure this way is your own run, not a documented number.

History: in 0.3.1, before the pins existed, a weak planner alone (`claude-haiku-4-5`) produced an
incomplete plan on 5 of 21 research scenarios and a stronger planner fixed all five (CHANGELOG 0.3.1;
pre-0.4.0 scenario set, not re-run). The same run found a stale SDK kwarg that made the old router
fall back to the mock silently; that silent fallback is why the layer now fails closed.

## 8. Call an MCP tool directly (in-process)

```python
import asyncio
from ceap.data.repositories import DatasetStore
from ceap.mcp.registry import build_in_process_client

client = build_in_process_client(DatasetStore())
tools = asyncio.run(client.discover_tools("execution"))
print([t["name"] for t in tools])

metrics = asyncio.run(client.invoke("execution", "get_execution_metrics", {
    "symbol": "AAPL", "start": "2026-09-18T14:00:00", "end": "2026-09-18T15:00:00", "dataset": "T06",
}))
print(metrics["implementation_shortfall_bps"], metrics["venue_statistics"]["ARCA"])
```

## 9. Run the MCP servers over stdio

Each server is a module you can run: `python -m ceap.mcp.market_data` (also `execution`,
`risk`, `engineering`, `knowledge`). To drive them with the official SDK from Python:

```bash
python scripts/run_stdio_mcp_demo.py     # discovers the 5 Stage-1 servers' tools and calls one
```

To point a generic MCP client (e.g. an IDE or desktop assistant) at a server, register the
command `python -m ceap.mcp.execution` with the working directory set to the repository and
the virtual environment on `PATH`.

## 10. Compute TCA metrics without any agent

```python
import asyncio
from ceap.analytics import StandardExecutionAnalytics, calculate_market_statistics
from ceap.data import DatasetStore, InMemoryExecutionRepository, InMemoryMarketDataRepository

ds = DatasetStore().get("T04")
er, mr = InMemoryExecutionRepository(ds), InMemoryMarketDataRepository(ds)
s, e = ds.window_start, ds.window_end

async def main():
    m = StandardExecutionAnalytics().calculate(
        await er.orders("AAPL", s, e), await er.executions("AAPL", s, e),
        await mr.quotes("AAPL", s, e), await mr.trades("AAPL", s, e), s, e)
    stats = calculate_market_statistics("AAPL", s, e, await mr.quotes("AAPL", s, e),
                                        await mr.trades("AAPL", s, e), await mr.order_books("AAPL", s, e))
    print(m.vwap, m.market_vwap, m.implementation_shortfall_bps, m.slippage_vs_vwap_bps)
    # Perold legs (they sum to the IS), the pre-trade estimate and the residual in SEs
    print(m.delay_cost_bps, m.spread_cost_bps, m.timing_cost_bps, m.opportunity_cost_bps, m.fees_bps)
    print(m.pretrade_estimate_bps, m.is_residual_bps, m.is_residual_z, m.markouts_bps)
    print(stats.average_displayed_depth, stats.realised_volatility_bps)

asyncio.run(main())
```

The individual functions (`vwap`, `implementation_shortfall_bps`, `slippage_bps`,
`realised_volatility_bps`, …) accept plain lists/arrays; see `tests/unit/test_analytics.py`
for known-answer examples.

## 11. Run attribution on your own numbers

```python
from ceap.analytics.attribution import attribute_causes
from ceap.domain.serialization import execution_metrics_from_dict, market_statistics_from_dict

window   = execution_metrics_from_dict(window_metrics_json)     # e.g. from your TCA system
baseline = execution_metrics_from_dict(baseline_metrics_json)
mkt_w    = market_statistics_from_dict(window_market_json)
mkt_b    = market_statistics_from_dict(baseline_market_json)
res = attribute_causes(window, baseline, mkt_w, mkt_b, engineering={"latency_ratio": 1.1})
print(res.primary, [(c.cause.value, round(c.score, 2), c.rationale) for c in res.ranked])
```

Missing fields become `nan` and the corresponding cause scores 0 — attribution degrades
gracefully rather than failing. Without `is_residual_bps` and `is_standard_error_bps` the
deterioration test falls back to the v0.4 1.5 bps delta (`res.basis == "legacy_delta"`). Pass
`peers=[PeerWindow(metrics, market), ...]`, the same clock window on previous sessions, for the
time-of-day-matched baseline and a peer-relative effect with a CI on every cause.

### TCA over many orders: the monthly report

```bash
ceap tca-report --sessions 20 --out best_ex.md        # labelled DEMONSTRATION (synthetic)
ceap tca-report --broker-gap-bps 3 --json             # plant a 3 bps broker gap and look for it
```

```python
from ceap.analytics.tca_aggregate import OutlierQueue, compare_groups, measure_universe
from ceap.data.tca_universe import generate_tca_universe

ms = measure_universe(generate_tca_universe(seed=1))
print([(g.key, round(g.mean_residual_bps, 2), g.flagged) for g in compare_groups(ms, "broker")])
queue = OutlierQueue.build(ms)          # residual beyond 2.5 SE, with reason codes
# closing needs a second principal: await queue.close(order_id, "analyst.a", gateway)
```

Refit the pre-trade model after changing the generator: `python scripts/train_pretrade.py`
(it rewrites `src/ceap/ml/pretrade_model.json` and `docs/quant/pretrade_model_card.md`).

## 12. Tune attribution thresholds

```python
from ceap.analytics.attribution import Thresholds, attribute_causes
strict = Thresholds(volatility_ratio=2.0, spread_ratio=1.6, price_move_bps=100.0)
res = attribute_causes(window, baseline, mkt_w, mkt_b, thresholds=strict)
```

To make them the platform default, pass a `Thresholds` into the quant agent (or subclass
`QuantAgent`) and re-run `ceap evaluate` — the evaluation table is how you find out what a
change does. Document the new values in `docs/evaluation/evaluation.md`.

## 13. Add a scenario template

In `src/ceap/data/scenarios.py` append a `ScenarioSpec` to `SCENARIO_TEMPLATES`:

```python
ScenarioSpec(
    id="T11", template="dark_pool_dry_up", name="Dark liquidity dries up",
    description="DARK1 fill rate collapses while lit venues are normal.",
    symbol="AAPL", seed=11, ground_truth=(Cause.VENUE_DEGRADATION,),
    degraded_venue="DARK1", degraded_fill_rate=0.2,
)
```

`all_scenarios()` fans it out to five symbols automatically (S51–S55). Run
`pytest tests/evaluation` — the coverage and primary-accuracy assertions tell you whether the
generator effect is strong enough for the attribution model to see it. New *causes* need a
`Cause` member, a scoring block in `attribute_causes`, and a line in `CAUSE_TEXT`.

## 14. Export a dataset to CSV / Parquet

```bash
ceap generate-data --scenario T06 --out data --format csv       # or --format parquet (pip install -e ".[quant]")
```

Files land under `data/market/`, `data/orders/`, `data/executions/`, `data/reference/`.
Query them with DuckDB:

```python
import duckdb
duckdb.sql("""
  select venue, count(*) fills, sum(quantity) qty, avg(latency_us) lat
  from 'data/executions/executions_T06_AAPL.csv' group by venue order by qty desc
""").show()
```

## 15. Add a knowledge document

Drop a Markdown file into `data/reference/knowledge/` (or point `CEAP_KNOWLEDGE_DIR`
elsewhere). Use headings — chunking splits on them. Check retrieval:

```python
from ceap.rag.retrieval import build_knowledge_base
kb = build_knowledge_base()
for hit in kb.search("dark pool minimum quantity", k=3):
    print(round(hit.score, 3), hit.chunk.title, "/", hit.chunk.section)
```

Retrieved passages become DOCUMENT evidence and appear in the report's POLICY CONTEXT
section when the plan calls `knowledge.search_documents`.

## 16. Add an MCP tool

In the relevant `build_server`:

```python
@server.tool("Return the top N prints by size in the window.", risk_level=RiskLevel.LOW)
async def get_block_trades(symbol: str, start: str, end: str, n: int = 10, dataset: str | None = None) -> dict:
    ds, r = repo(dataset)
    s, e = window_of(ds, start, end)
    trades = sorted(await r.trades(symbol, s, e), key=lambda t: -t.quantity)[:n]
    return {"symbol": symbol, "count": len(trades), "items": [to_jsonable(t) for t in trades]}
```

The schema is derived from the signature; the tool is discovered automatically as
`market_data.get_block_trades`, appears in the planner's catalogue and in `/tools`, and is
subject to policy. Mark mutating tools `read_only=False` — they will then need
`tools:write`.

## 17. Add a new MCP server backed by a real data source

1. Implement `MarketDataRepository` / `ExecutionRepository` (or your own interface) over the
   real store (Parquet + DuckDB, PostgreSQL, a vendor API).
2. Build an `MCPServerDefinition` whose tools call it and return JSON-able dicts.
3. Register it in `ceap.mcp.registry.build_default_servers` (or construct
   `InProcessMCPClient({...})` / `StdioMCPClient([...])` yourself and pass `mcp_client=` to
   `Platform`).
4. Add its tool ids to the canonical plan in `ceap.llm.client.canonical_plan` if the
   deterministic planner should use it, and to the evidence-type map in
   `ceap.mcp.adapter` if you want a specific evidence prefix.

## 18. Add a specialist agent

```python
from ceap.agents.base import BaseAgent
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding

class VenueRoutingAgent(BaseAgent):
    agent_id = "routing"

    async def execute(self, context: AgentContext) -> AgentResult:
        w = self.window(context)
        venues, ev = await self.ensure(context, "execution.get_venue_statistics", w.window_args)
        dark = venues["venues"].get("DARK1")
        findings = ()
        if dark:
            findings = (Finding.create(f"DARK1 captured {dark['volume_share']:.1%} of window volume.", ev, 0.8,
                                       category="EXECUTION", produced_by=self.id),)
        return AgentResult(agent_id=self.id, success=True, findings=findings, output={"dark": dark})
```

Register it in `Platform._agents()` and add `"routing"` to `ALLOWED_AGENTS` in
`ceap.agents.planner`; a plan step `{"type": "AGENT_CALL", "agent_id": "routing"}` then runs it.
Findings must cite evidence ids or validation will drop them.

## 19. Restrict what a principal may do

```python
from ceap.policy.engine import RulePolicyEngine
platform = Platform()
platform.policy = RulePolicyEngine(
    allowed_symbols=frozenset({"AAPL", "MSFT"}),
    denied_tools=frozenset({"risk.calculate_stress"}),
    max_page_limit=1000,
)
```

Per request, `InvestigationRequest(attributes={"allowed_symbols": {"NVDA"}})` narrows the
universe further. Roles and capabilities live in `ceap.policy.permissions`.

## 20. Cancel a running investigation

```bash
curl -s -X POST localhost:8000/investigations/TASK-xxxx/cancel -H "X-API-Key: dev-trader-key"
```

or in Python `platform.cancel(task_id)`. The harness checks the token before every tool call
and ends in `CANCELLED` with the reason in `result.error`.

## 21. Read the trace and policy log

```python
for span in result.trace["spans"]:
    print(span["name"], span["duration_ms"], span["status"])
for entry in result.policy_log:
    print(entry["tool_id"], entry["decision"], entry["rule"], entry["reason"])
print([h["to"] for h in result.state_history])
```

## 22. Inspect the critic's assessment

```python
critique = result.report.critique
print(critique["overall"])
for a in critique["assessments"]:
    print(a["finding_id"], a["original_confidence"], "->", a["adjusted_confidence"], a["notes"])
print(critique["contradictions"], critique["narrative_number_warnings"], critique["narrative_evidence_warnings"])
```

## 23. Run the evaluation and read the table

```bash
ceap evaluate            # prints one line per scenario, then the summary JSON; exit 1 below 90 % accuracy
ceap evaluate --limit 10
```

Columns: `OK`/`MISS`, scenario id, symbol, template, primary cause found, false positives,
duration. The summary reports `primary_accuracy`, `coverage`, `false_positive_rate`,
`completed`, `unresolved_findings`, `number_warnings`, `mean_duration_ms`.

## 24. Run only the fast tests

```bash
pytest --ignore=tests/evaluation          # ~45 s
pytest tests/unit                          # a few seconds
pytest tests/evaluation                    # the 50-scenario run, ~1 minute
ruff check src tests && ruff format --check src tests
```

## 25. Export traces to OpenTelemetry

```bash
pip install -e ".[observability]"
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
ceap serve
```

`build_tracer()` returns an `OpenTelemetryTracer` when the endpoint is set and the SDK is
installed; the API's in-memory trace is unchanged. Prometheus scrapes `GET /metrics`.

## 26. Run in Docker

```bash
docker build -t ceap .
docker run --rm -p 8000:8000 -e CEAP_API_KEYS="prod-key:trader" -e ANTHROPIC_API_KEY=... ceap
```

The image sets `CEAP_ENV=prod` and `CEAP_AUTO_APPROVE=false`.

## 27. Publish the docs site

See `docs/GITHUB_PAGES.md`: Settings → Pages → Deploy from a branch → `main` / `/docs`. The
landing page is `docs/index.html`; everything else links to rendered Markdown on GitHub.

## 28. Run a signal research proposal (Stage 2)

```bash
ceap research "Does 12-1 momentum work on R01? Evaluate it out-of-sample with costs." --role quant
```

The signal and dataset are parsed from the question when omitted ("momentum" → `momentum_12_1`,
"reversal"/"mean reversion" → `reversal_5`, "low vol" → `low_vol_60`; a ticker like `R04` or
`RS12`). `--dataset` picks the research scenario (R01–R07 templates, RS01–RS21 concrete).
`--role` must have the `research` capability (`quant` or `admin`); `viewer`/`trader`/`engineer`
do not. The proposal's VERDICT section carries `PROMOTE` or `REJECT` with any flags
(`NO_ALPHA`, `OOS_DECAY` (called `OVERFIT` before 0.4.0), `COST_DRAG`, `CONCENTRATION`, `LIMIT_BREACH`).

## 29. Run a research proposal from Python

```python
import asyncio
from ceap.platform import Platform, default_research_request

platform = Platform()                                          # mock LLM unless ANTHROPIC_API_KEY is set
result = asyncio.run(platform.research(default_research_request("R01")))

print(result.state, result.duration_ms)                        # HarnessState.COMPLETED
print(result.report.proposal["verdict"], result.report.proposal["flags"])   # PROMOTE []
print(result.report.narrative)
```

`default_research_request(dataset, signal=None, stage_orders=False, roles=None)` scopes the
request to the scenario's own signal and rebalance frequency; pass your own `ResearchRequest`
for a custom signal, window or gross notional.

## 30. Stage paper orders behind a human approval (Stage 2)

```python
import asyncio
from ceap.platform import Platform, default_research_request

platform = Platform()                                           # admin: has tools:write + trading:execute
result = asyncio.run(platform.research(
    default_research_request("R01", stage_orders=True, roles=frozenset({"admin"}))
))
staging = result.report.proposal["staging"]
print(staging["staged"], staging["staging_id"], staging["count"])
```

Order staging needs *two* separately-decided `HUMAN_APPROVAL` steps the harness appends itself
(the plan cannot schedule either): one for the research proposal, one for the order preview.
With `CEAP_AUTO_APPROVE=false` both show up in `GET /approvals` — see recipe 6 — the second
carries `arguments.orders_preview` instead of `arguments.proposal`. Declining the second still
completes the task; only the STAGED ORDERS section changes. `execution.stage_orders` never
routes anywhere. It only builds paper orders in a `StagedOrderBook`, which the platform backs with its
SQLite database (`staged_orders`, one row per `staging_id`). Before staging it runs the pre-trade
checks (restricted, hard to borrow, per-name and gross limits) and returns any `risk_blocks`. While
the kill switch is set it refuses (recipe 38).

## 31. Use the research API

```bash
ceap serve
curl -s -X POST http://127.0.0.1:8000/research -H "X-API-Key: dev-quant-key" \
     -H "Content-Type: application/json" \
     -d '{"question": "Does 12-1 momentum work on R01? Evaluate it out-of-sample with costs."}'
# {"task_id": "...", "status": "RUNNING", "links": {"report": "/research/TASK-.../report", ...}}

curl -s http://127.0.0.1:8000/research/TASK-.../report -H "X-API-Key: dev-quant-key"
```

`dev-admin-key` is the only default key with `trading:execute`; pass `"stage_orders": true` in
the body as admin to also stage paper orders. `GET /research-scenarios?all=true` lists all 21
concrete research scenarios the way `GET /scenarios?all=true` lists the 50 execution ones.

## 32. Add a new signal

```python
# src/ceap/analytics/signals.py
def my_signal(close, volume=None):
    ...  # (n_days, n_symbols) -> same-shape array; NaN during warm-up

SIGNALS["my_signal"] = SignalSpec(
    "my_signal", "one-line description", lookback_days=..., default_horizon_days=...,
    default_rebalance_days=..., fn=my_signal,
)
```

`evaluate_signal`, `run_backtest` and `target_portfolio` all resolve signals through this
registry, so a new entry is immediately usable from `ceap research --signal my_signal` and every
MCP tool. Add a `ResearchScenarioSpec` template with ground truth
(`ceap.data.research_scenarios`) if you want it exercised by `ceap evaluate --suite research`.

## 33. Add a research scenario template

Same shape as recipe 13, one level up: add a `ResearchScenarioSpec` to
`RESEARCH_TEMPLATES_RAW` in `ceap/data/research_scenarios.py` with generator parameters only
(`mechanism="drift"` + `premium_bps`, or `mechanism="transient"` + `transient_share`;
`oos_premium_multiplier`, `spread_multiplier`, `concentration_names`, `crash`). Do not write a
verdict: `ceap.data.ground_truth.derive_ground_truth` computes it from the parameters and marks
near-threshold scenarios (`ceap research-scenarios` prints both). It joins `all_research_scenarios()` (× 3 seeds) automatically,
so `ceap evaluate --suite research` and `tests/evaluation/test_research_scenarios.py` pick it up.

## 34. Day-to-day development: bootstrap, test tiers, lock, release

```bash
scripts/bootstrap.sh                                  # .venv from the hashed lock
pytest -n auto -m "not nightly and not slow"          # quick local loop
pytest -n auto -m "not nightly" --cov=ceap --cov-branch --cov-fail-under=85   # what PR CI runs
CEAP_NIGHTLY=1 HYPOTHESIS_PROFILE=nightly pytest -n auto -p randomly    # what nightly runs: random order, 1000 examples
pytest tests/unit                                     # hermetic: reads nothing outside src/ and tests/
pytest tests/repo                                     # repository integrity: scripts, docs, workflows
python scripts/check_banned_words.py                  # must print "clean"
python scripts/check_contracts.py origin/main         # lists any public ceap.domain signature change
```

Every test has a 120 s timeout (`pytest-timeout`); `slow` tests get 600 s and `nightly` tests
1800 s unless they set `@pytest.mark.timeout(...)` themselves. The 20 slowest tests are printed at
the end of every run. Nightly-marked tests skip unless `CEAP_NIGHTLY=1`.

Adding a dependency: edit `pyproject.toml`, then run the `uv pip compile ...` command printed in
the header of `requirements.lock` (uv is free: `pip install uv`). Commit both files; the
`lock-fresh` CI job checks that they agree. Never edit the lock by hand.

Releasing (maintainer only): bump the version in `pyproject.toml` and `src/ceap/__init__.py`, rename
`## Unreleased` to `## [X.Y.Z] - date` in `CHANGELOG.md`, merge, wait for CI on `main` to go green,
then push the tag `vX.Y.Z`. `release.yml` runs `scripts/release_check.py` and builds nothing unless
the latest run of the `CI` workflow on that commit is green (since 0.7.1 other workflows, such as the
daily paper job, do not count). Local dry run: `python scripts/release_check.py --tag vX.Y.Z --skip-checks`.

## 35. Measure the PROMOTE hurdle (null sweep)

```bash
ceap evaluate --suite null --seeds 50
```

Prints the false-PROMOTE rate of the full 24-configuration search on pure-noise data at the
committed hurdle, with its 95 % binomial band. `--seeds 500 --workers 6 --write-thresholds`
re-derives the hurdle and writes it with the sweep hash to `data/reference/research_thresholds.json`.

## 36. Profitability table and capacity sweep

```bash
ceap profitability --dataset R03
```

Breakeven cost per unit turnover, cost share by AUM (10 M to 500 M), drawdown depth and duration,
calendar-year returns, worst months and alpha after the market factor.

## 37. Backtest under different execution assumptions

```python
from ceap.analytics.backtest import run_backtest
from ceap.data.historical import HistoricalStore
ds = HistoricalStore().get("R03")
for lag in (0, 1, 2):
    print(lag, run_backtest(ds, "reversal_5", rebalance_days=5, execution_lag_days=lag).period("full").sharpe)
run_backtest(ds, "reversal_5", rebalance_days=5, fill="open")  # next open; lag must be >= 1
```

`lag=0` is the 0.3.x same-close look-ahead, kept only for comparison.

## 38. Keep state across restarts (SQLite)

```bash
CEAP_DB_PATH=var/ceap.db ceap serve       # outside dev this is the default path
ceap db status                             # schema version, size, audit-chain check
ceap db backup var/ceap-2026-11-07.db      # online backup, then verifies the copy
ceap db verify-chain                       # exit 1 if any audit row was edited or deleted
ceap db prune                              # apply CEAP_RETENTION_* and CEAP_DB_MAX_BYTES
```

### Keep the audit trail tamper-evident (0.8.0)

```bash
export CEAP_AUDIT_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"  # keep it out of the repo
ceap db anchor --out audit/audit_head.json        # chain heads + audit_head.json.sha256
git add audit/audit_head.json audit/audit_head.json.sha256 && git commit -m "audit: anchor"
ots stamp audit/audit_head.json                   # optional, free: OpenTimestamps proof (run by hand)
ceap db verify-chain --anchor audit/audit_head.json   # exit 1 if any anchored row changed or vanished
```

Audit and paper ledger rows are HMAC-keyed. Without `CEAP_AUDIT_HMAC_KEY` the platform uses a
published development key and `ceap db status` shows `"dev_key": true`. That is fine for the demo and
the public paper record, but it protects nothing. To rotate, put the new key in `CEAP_AUDIT_HMAC_KEY`
and list the old ones in `CEAP_AUDIT_HMAC_OLD_KEYS` (comma separated; `dev-default` names the
development key). The key cannot stop someone who holds it from rebuilding the chain. The anchor
can: commit one after every working day and any rebuild shows up as a changed head.

### Stop all paper staging (kill switch)

```bash
ceap db kill-switch set --reason "venue outage" --actor ops   # audited; paper job and stage_orders stop
ceap db kill-switch status
ceap db kill-switch clear --reason "resolved" --actor ops     # audited
```

After a crash or `kill -9`, the next start marks in-flight tasks `INTERRUPTED` and pending approvals
`EXPIRED`; their status and stored result stay readable (`GET /investigations/{id}`, `/result`).

## 39. Replay a task byte for byte

```bash
ceap replay bundle.json --record --dataset T01   # run offline, write the bundle
ceap replay bundle.json                          # exit 0 when the replay equals the stored result
```

The bundle holds the request, every tool output, redacted model I/O, prompt hashes, approval
decisions and the clock seed. A mismatch prints the first differing JSON path.

## 40. Scrape metrics and correlate logs

```bash
curl -s localhost:8000/metrics -H "X-API-Key: dev-auditor-key"   # needs metrics:read
curl -si localhost:8000/health -H "X-Request-ID: my-trace-1"     # echoed back; on every log line
```

## 41. Hold API keys as hashes only

```bash
python -c "from ceap.api.auth import hash_api_key; print(hash_api_key('a-long-random-key'))"
CEAP_API_KEY_HASHES='sha256$...$...:auditor:2027-01-31' ceap serve   # optional expiry date
```

## 42. Produce the evaluation report and record a release

```bash
ceap evaluation-report --quick                                          # smoke run, 3 scenarios per sweep
ceap evaluation-report --json docs/evaluation/latest.json               # full run (about 20 minutes)
ceap evaluation-report --json docs/evaluation/latest.json --append-history 0.6.0
python scripts/check_docs.py                                            # docs numbers vs latest.json
```

Live-only metrics always print `UNKNOWN / NOT RUN LIVE`; a `--quick` run is refused by
`--append-history`. After a full run, regenerate the table in `docs/evaluation/evaluation.md`
(the block between the `evaluation-report` markers) from `latest.json`, or `check_docs.py` fails.
Golden-set edits: `python scripts/build_golden_set.py`, bump `VERSION`, label the PR `golden-change`.

## 43. Paper-trade a signal and keep a forward record

paper, synthetic or free daily data, not trading.

```bash
ceap paper run-day --date 2026-11-13 --db var/paper/paper.db   # processes 2026-11-09 .. 11-13
ceap paper run-day --date 2026-11-13 --db var/paper/paper.db   # no-op: each day runs once
ceap paper verify  --db var/paper/paper.db                      # hash chains + reconciliation
ceap paper report  --db var/paper/paper.db                      # Sharpe, 95 % CI, minimum days
ceap paper export  --db var/paper/paper.db --out forward_log.csv
ceap paper backfill --from 2026-06-01 --to 2026-10-30 --db var/paper/paper.db  # simulated log only
```

Each session writes `var/paper/reports/<book>/<date>.md` and `.json`: P&L attribution (signal,
drift, financing, borrow, costs), fills with their staging and approval ids, every reject, cancel and
risk block, and the reconciliation. Stop all new orders with `ceap db --path var/paper/paper.db
kill-switch set --reason "..."` (audited, persists until cleared; recipe 38) or for one run with
`CEAP_PAPER_HALT="reason" ceap paper run-day ...`: working orders are cancelled and the reason is
logged. Ledger and day rows are HMAC-keyed (`CEAP_AUDIT_HMAC_KEY`; recipe 38). `ceap db anchor` also
records each paper book's chain heads. Limits (restricted and
hard-to-borrow lists, per-name and gross caps, % ADV, price bands, drift tolerance) live under `risk` in
`config/paper.json`. Exit codes: 3 not a session (holiday or weekend), 4 reconciliation break (nothing
committed; the break report is printed).

Outside `CEAP_ENV=dev`, add `--allow-paper-auto` to let the `paper-auto` principal approve the
staging; the decision is written to the audit log either way. Change the signal, capital or costs in
`config/paper.json`. Never move `forward_start` earlier.

The feed is synthetic, so this record checks operations and controls, not profitability. The
Stooq feed (`"feed": "stooq"`) needs a local, git-ignored cache under `data/real_cache/stooq/` (Stooq
blocks scripted downloads, so files must be placed by hand), refuses rows up to 2025-12-31, and cannot
produce a 12-1 signal before about January 2027. The scheduled workflow runs because the owner set
the repository variable `PAPER_DAILY_ENABLED=true` on 2026-11-08 (D22); the forward record starts on
2026-11-09.

If the record stops, you hear about it: a failed `paper-daily` run opens (or comments on) an issue
labelled `paper-failure`, and the weekly `paper-staleness` workflow opens a `paper-stale` issue when
the `forward-log` head is more than 2 NYSE sessions old. Check by hand:

```bash
git fetch origin forward-log
git show FETCH_HEAD:forward_log.csv > forward_log.csv
python scripts/check_forward_log_staleness.py --csv forward_log.csv   # exit 1 = stale
```

## 44. Run a parent order through the simulated book

```python
from ceap.execution.algo import ParentOrder, execute
from ceap.execution.child import ChildPolicy
from ceap.execution.lob import BUY, LOBSimulator
from ceap.execution.schedule import POV, TWAP, VWAP, AlmgrenChriss

parent = ParentOrder(BUY, 3_000, start_ms=60_000, end_ms=660_000, n_slices=10)  # 10 minutes
for sched in (TWAP(), VWAP(0.4, 0.45), POV(0.10, 0.03, total=3_000),
              AlmgrenChriss(sigma=0.3, eta=1e-4, gamma=1e-6, horizon=1.0, risk_aversion=1e-3)):
    r = execute(LOBSimulator(seed=100), parent, sched, ChildPolicy(urgency=0.5))
    print(sched.name, r.executed, round(r.passive_share, 2), r.components.as_dict())
```

The book is a price-time FIFO queue with latency; passive children fill only when a taker trades
through the queue ahead of them, and the bot's own fills move the price (propagator impact). The cost
is the same Perold split the post-trade TCA uses. One seed is one noisy draw: compare schedulers over
many seeds (docs/execution/execution.md, section 3).

## 45. Fit market impact and compare it with the truth

```python
from ceap.execution.impact import fit_propagator
from ceap.execution.lob import LOBSimulator

sim = LOBSimulator(seed=3)
for t in range(0, 600_000, 2_000):           # buy 200 every 2 s for 10 minutes
    sim.run_until(t)
    sim.submit(1, 200)
sim.run_until(900_000)
flow = sim.agent_flow_grid(1_000, 0, 900_000)
fit = fit_propagator(sim.mid_grid(1_000, 0, 900_000, efficient=True), flow, dt_s=1.0)
print(fit.compare(sim.p.kernel))              # estimate, SE, truth and z for each parameter
```

Use `efficient=True` to check the mechanism; on the quoted mid (`efficient=False`) the bid-ask bounce
biases the split towards a large, fast-reverting impact. One-directional flow like this identifies
the permanent part poorly: vary the side for a cleaner fit.

## 46. Markout curves and a venue routing table from fills

```python
from ceap.data.scenarios import get_scenario
from ceap.data.synthetic import SyntheticMarketGenerator
from ceap.execution.markouts import markout_curves, routing_table

ds = SyntheticMarketGenerator().generate(get_scenario("venue_degradation"))
for p in markout_curves(ds.executions, ds.quotes, horizons=(1, 30, 300, "close"), n_boot=300):
    print(p.group, p.horizon, p.n, round(p.mean_bps, 2), (round(p.ci_low_bps, 2), round(p.ci_high_bps, 2)))
for row in routing_table(ds.executions, ds.quotes, horizon=60):
    print(row.venue, row.verdict, round(row.markout_bps, 2), round(row.suggested_weight, 3))
```

Positive markout = the price moved our way after the fill. A venue is `adverse` only when its whole
CI is below zero.

## 47. Predicted versus realised cost, weekly

```python
from datetime import date
from ceap.execution.cost_loop import CostLoop, CostObservation

loop = CostLoop("var/costs.sqlite")               # or ":memory:"
pred = loop.predict(quantity=40_000, adv=1_000_000, sigma_daily_bps=120.0)
loop.record(CostObservation(date(2026, 11, 9), "P-1", 40_000, 1_000_000, 120.0, pred, realised_bps=17.2))
# ... record each parent as it completes, then once a week:
calibration, drift = loop.weekly(as_of=date(2026, 11, 13))
print(calibration, drift)                         # new Y and exponent; mean residual in SEs
```

`weekly` needs at least three observations to fit; `drift.alert` is `|z| >= 2` on the last seven days.
`reliability_table(loop.observations())` gives the rows for a reliability plot.

## 48. Regression cause attribution and the trading rules

```bash
CEAP_ATTRIBUTION=regression ceap investigate "Why did AAPL execution deteriorate between 14:00 and 15:00?" --dataset T03 --json result.json
```

(T03 is the wide-spreads scenario) or `attribute_causes(..., method="regression")` in Python. The heuristic verdict is unchanged; the
regression's bps share per cause with CIs appears under `regression`. With only five matched peer
sessions it leans on its ridge prior and says so.

```python
from ceap.execution.auction import AuctionOrder, uncross
from ceap.execution.rules import LULDMonitor, luld_bands, round_lot_size, round_to_tick

print(round_to_tick(10.019, side=1), round_lot_size(400.0), luld_bands(100.0, seconds_since_open=600))
print(uncross([AuctionOrder(1, 500), AuctionOrder(-1, 400, 9.99), AuctionOrder(-1, 400, 10.01)], reference=10.0))
```

The tick, lot and LULD numbers are the published rules as recalled; check the current text before
relying on them.
