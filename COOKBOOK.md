# COOKBOOK.md — Recipes

Short, copy-pasteable recipes for the things people actually do with the platform. Every
recipe runs offline with the deterministic model unless it says otherwise. Paths are relative
to the repository root; activate your virtual environment first (`pip install -e ".[dev]"`).

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

---

## 1. Run an investigation from the CLI

```bash
ceap investigate "Why did our execution quality for AAPL deteriorate between 14:00 and 15:00?" --dataset T06
```

The symbol and window are parsed from the question ("between 14:00 and 15:00", "from 2pm to
3:30pm"; naive times are Europe/London). `--dataset` picks the scenario (T01–T10 templates,
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
from zoneinfo import ZoneInfo
from ceap.platform import InvestigationRequest

LONDON = ZoneInfo("Europe/London")
req = InvestigationRequest(
    question="Analyse MSFT VWAP execution and identify the main sources of slippage.",
    symbol="MSFT",
    window_start=datetime(2026, 9, 18, 14, 0, tzinfo=LONDON),
    window_end=datetime(2026, 9, 18, 15, 0, tzinfo=LONDON),
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
curl -s localhost:8000/investigations/TASK-xxxx           -H "X-API-Key: dev-viewer-key"
curl -s localhost:8000/investigations/TASK-xxxx/report    -H "X-API-Key: dev-viewer-key"
curl -s "localhost:8000/investigations/TASK-xxxx/result?include_trace=true" -H "X-API-Key: dev-viewer-key"
curl -s localhost:8000/investigations/TASK-xxxx/trace     -H "X-API-Key: dev-viewer-key"
```

Keys → roles come from `CEAP_API_KEYS=key:role,...`. Viewer can read; trader/quant/engineer
can investigate; admin can decide approvals.

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

Unanswered approvals time out (120 s by default in `QueuedApprovalGateway`) and the step is
marked DENIED; the investigation continues without it.

## 7. Use a real model

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=sk-ant-...
export CEAP_LLM_MODEL=claude-haiku-4-5             # optional, cheap narrator/critic
export CEAP_LLM_PLANNING_MODEL=claude-sonnet-5     # recommended: a stronger planner
ceap investigate "..." --dataset T06
```

`CEAP_LLM_PROVIDER=auto|mock|anthropic` overrides detection. If the model call fails the
router falls back to the deterministic mock and counts it in `router.usage["fallbacks"]`.
The planner's `rejected_steps` and the report's `narrative_number_warnings` tell you how the
model behaved.

For Stage 2 research (`ceap research ...`), the planning model matters more than the narrator
model: a weak planner alone (tested — `claude-haiku-4-5`) produced an incomplete plan on 5 of 21
research scenarios; pairing it with `CEAP_LLM_PLANNING_MODEL=claude-sonnet-5` fixed all five (see
CHANGELOG 0.3.1). `docs/evaluation/evaluation.md` has the full before/after numbers. This also
caught two real bugs the offline mock had never exercised — check the installed `anthropic`
package version if a "live" run's `llm_model` in the result keeps coming back as
`mock-deterministic-v1` with no error: a stale SDK kwarg silently triggering the router's
fallback is exactly what happened in 0.3.1.

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
gracefully rather than failing.

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
(`NO_ALPHA`, `OVERFIT`, `COST_DRAG`, `CONCENTRATION`, `LIMIT_BREACH`).

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
routes anywhere — it only ever builds paper orders in an in-memory `StagedOrderBook`.

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
`RESEARCH_TEMPLATES` in `ceap/data/research_scenarios.py` with the effect you want embedded
(`premium_bps`, `oos_premium_multiplier` for a regime break, `spread_multiplier` for cost drag,
`concentration_names` for a concentrated book) and the `expected_verdict`/`expected_flags` you
expect `assess_research` to reach. It joins `all_research_scenarios()` (× 3 seeds) automatically,
so `ceap evaluate --suite research` and `tests/evaluation/test_research_scenarios.py` pick it up.
