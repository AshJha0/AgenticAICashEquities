# Cash Equities Agentic Platform (CEAP)

An enterprise-style **Agentic AI platform** that investigates, analyses and explains cash-equity
trading and market behaviour. Ask it:

> *"Why did our execution quality for AAPL deteriorate between 14:00 and 15:00 yesterday?"*

and it plans an investigation, pulls orders, fills, market data, order-book depth, venue statistics,
technology telemetry and enterprise runbooks through **MCP**, computes every number with
**deterministic quant analytics**, lets specialist agents interpret the evidence, has an
independent **critic** challenge the findings, and writes an **auditable report** in which every
claim points at an evidence record.

```
Python 3.11+  ·  MCP  ·  LLM (Anthropic or offline mock)  ·  multi-agent harness  ·  FastAPI  ·  RAG  ·  policy engine  ·  OpenTelemetry-ready
```

The architectural boundary is enforced in code, not just described:

| Concern | Owner | Never |
|---|---|---|
| Reasoning, planning, narrative | LLM | computes a metric, invents an evidence id |
| VWAP, IS, slippage, impact, volatility, liquidity, attribution | `ceap.analytics` (pure Python/NumPy) | – |
| Capability interface | MCP servers + `MCPToolAdapter` | – |
| Control plane (state machine, retry, timeout, approval, trace) | `ceap.harness` | lets an agent control the harness |
| Authority | `ceap.policy` | – |
| Auditability | `Evidence` / `Finding` | a finding without resolvable evidence survives validation |

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 1. run an investigation from the CLI (offline, deterministic mock LLM)
ceap investigate "Analyse our AAPL execution between 14:00 and 15:00 and explain why implementation shortfall increased." --dataset T06

# 2. run the API and open http://127.0.0.1:8000/docs
ceap serve
curl -s -X POST http://127.0.0.1:8000/investigations -H "X-API-Key: dev-trader-key" -H "Content-Type: application/json" \
     -d '{"question": "Why did AAPL execution deteriorate between 14:00 and 15:00?", "dataset": "T05"}'

# 3. run the tests (unit, integration, MCP, agent, adversarial, 50-scenario evaluation)
pytest

# 4. evaluate the platform against the 50-scenario ground truth
ceap evaluate
```

To use a real model, set `ANTHROPIC_API_KEY` (and optionally `CEAP_LLM_MODEL`) and install the
`llm` extra: `pip install -e ".[llm]"`. Without a key the platform uses a deterministic mock so
every test and demo runs offline; the router falls back to the mock automatically if the model
call fails.

### Example output (scenario T06, technology latency)

```
AAPL EXECUTION INVESTIGATION 14:00-15:00 London

EXECUTIVE SUMMARY

Headline execution cost did not deteriorate versus the baseline (implementation shortfall delta
-35.9 bps, slippage versus interval VWAP delta +0.4 bps); however the evidence identifies technology
latency as a material anomaly during the window. ...

PRIMARY OBSERVATIONS

1. Primary anomaly: technology latency / rejects in the execution path (score 0.97; execution latency
   ratio vs baseline = 9.98 (threshold 3.0); reject rate = 10.0%). [confidence 0.94]
2. Order-gateway acknowledgement latency was 10.0x the baseline (p99 9828 us, SLO breached). [confidence 0.91]
3. Deployment CHG-40388 (smart-order-router 2.14.1: Routing latency budget and venue timeout changes)
   went live at 2026-09-18T13:52:00+01:00 - shortly before the window. [confidence 0.85]
...
TECHNOLOGY

Execution latency: Elevated (ratio 9.98 vs baseline)
Reject rate: Elevated (10.0%)
Deployments in scope: 1; error logs: 41
...
EVIDENCE

- CHANGE-3a3ac7ec
- ENG-1302fd13
- EXEC-5672d0d8
- TCA-16395dc6
...
```

---

## Architecture

```
                         ┌──────────────────────┐
                         │        User          │  Trader / Quant / Dev
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │    API / Gateway     │  FastAPI · API keys · RBAC (ceap.api)
                         └──────────┬───────────┘
                                    ▼
              ┌─────────────────────────────────────────┐
              │             AGENT HARNESS               │  ceap.harness
              │  Planner → Plan validation → Steps      │  state machine · TaskGroup batching
              │  Policy · Approval · Retry · Timeout    │  cancellation · memory · tracing
              │  Critic → Evidence validation → Report  │  governance steps enforced
              └──────────────────┬──────────────────────┘
                                 ▼
        Market · Execution · Quant · Risk · Engineering · Critic · Reporter   (ceap.agents)
                                 ▼
                         ┌────────────────┐
                         │  ToolRegistry  │  MCPToolAdapter → MCPClient (in-process or stdio)
                         └───────┬────────┘
     ┌──────────────┬────────────┼─────────────┬──────────────┐
     ▼              ▼            ▼             ▼              ▼
 market_data    execution      risk       engineering     knowledge        (ceap.mcp.*)
 quotes/trades  orders/fills   positions  logs/metrics    runbooks/policy
 order book     TCA metrics    limits     deployments     (RAG)
 statistics     venue stats    stress     latency
                                 ▼
                       ┌─────────────────────┐
                       │  Quant Analytics    │  VWAP · TWAP · IS · slippage · spreads
                       │  (deterministic)    │  impact · liquidity · volatility · attribution
                       └─────────────────────┘
```

### Investigation lifecycle

```
CREATED → PLANNING → VALIDATING_PLAN → EXECUTING ⇄ AWAITING_APPROVAL
        → CRITIQUING → VALIDATING_EVIDENCE → FINALISING → COMPLETED   (| FAILED | CANCELLED)
```

1. **Understand request** – symbol and window are parsed deterministically (`ceap.api.parsing`) so
   data scope is policy-checked before any model sees the text.
2. **Plan** – the Planner asks the LLM for a JSON plan restricted to the discovered tool catalogue;
   unknown tools/agents/arguments are stripped, and unusable output falls back to a canonical plan.
3. **Validate plan** – structure, tool existence and a policy pre-check of every tool call; a DENY
   fails fast before anything runs. Critic → validation → finalise are appended if missing.
4. **Execute** – consecutive independent tool calls run concurrently (`asyncio.TaskGroup`); each call
   passes policy → (approval) → retry/timeout → tool → memory → trace. Agents call tools only
   through the same path.
5. **Critique** – deterministic checks (evidence resolves, anomalies corroborated by attribution,
   cross-agent contradictions, thin evidence capped) plus an optional LLM critique that can only
   lower confidence.
6. **Validate evidence** – findings citing unknown evidence are dropped and recorded.
7. **Finalise** – the Reporter hands structured facts to the LLM; the narrative is then audited:
   every number must be traceable to the facts and every evidence id must exist.

---

## Repository layout

```
src/ceap/
├── domain/        Task · Plan · PlanStep · Tool · ToolRequest/Result · Evidence · Finding · Order · Execution ·
│                  Quote · Trade · OrderBook · ExecutionMetrics · PolicyDecision · repositories · reports
├── analytics/     vwap · twap · implementation_shortfall · slippage · market_impact · liquidity ·
│                  volatility · market_statistics · execution_metrics (TCA) · attribution
├── data/          synthetic market model (6 symbols, 5 venues), 10 scenario templates x 5 symbols = 50 scenarios
├── mcp/           server definition (in-process + FastMCP export), client (in-process + stdio), adapter, registry,
│                  market_data/ execution/ risk/ engineering/ knowledge/ servers (each runnable: python -m ceap.mcp.<name>)
├── llm/           LLMClient · MockLLMClient (deterministic) · AnthropicLLMClient · router · prompts
├── harness/       engine (AgentHarness) · state_machine · executor · retry · cancellation · memory
├── agents/        planner · market · execution · quant · risk · engineering · critic · reporter
├── policy/        rule engine · RBAC permissions · approval gateways (auto / queued / deny)
├── rag/           hashing TF-IDF embedder · chunking · KnowledgeBase
├── observability/ tracing (in-memory + OpenTelemetry) · metrics (Prometheus text) · JSON logging
├── api/           FastAPI app · routes · auth · schemas · request parsing
├── platform.py    composition root
├── evaluation.py  50-scenario evaluation
└── cli.py         ceap investigate | scenarios | evaluate | generate-data | serve
tests/             unit · integration · mcp · agent · evaluation · adversarial
data/reference/knowledge/   runbooks, TCA methodology, venue config, limits, policies (RAG corpus)
docs/              architecture · threat-model · api · evaluation
```

---

## Scenarios and evaluation

| Template | Ground truth | Effect injected in 14:00–15:00 |
|---|---|---|
| T01 normal_vwap | NORMAL | none |
| T02 high_volatility | MARKET_VOLATILITY | realised vol x3 |
| T03 wide_spreads | WIDE_SPREADS | quoted spread x2.5 |
| T04 low_liquidity | LOW_LIQUIDITY | displayed depth x0.3 |
| T05 venue_degradation | VENUE_DEGRADATION | ARCA fill rate 0.45, +4 bps slippage |
| T06 technology_latency | TECHNOLOGY_LATENCY | gateway latency x10, 10% rejects, SOR deployment 13:52 |
| T07 market_data_anomaly | MARKET_DATA_ANOMALY | 15% stale quotes, 20 crossed quotes, feed gaps |
| T08 large_parent_order | LARGE_ORDER_IMPACT | parent order x10 → ~35% participation |
| T09 unexpected_price_move | PRICE_MOVEMENT | +200 bps trend over 10 minutes |
| T10 mixed_market_technology | MARKET_VOLATILITY + TECHNOLOGY_LATENCY | vol x3 and latency x8 |

Each template runs across AAPL, MSFT, NVDA, AMZN and META with distinct seeds (S01–S50).
`ceap evaluate` reports primary-cause accuracy, ground-truth coverage, false-positive rate,
completion rate, unresolved-evidence count and narrative number-audit warnings. The evaluation
test asserts ≥90 % primary accuracy and coverage, 100 % completion and zero unresolved evidence;
the current build scores 50/50 on primary cause.

---

## MCP servers

Every server is defined once (`MCPServerDefinition`) and can be used in-process or exported to a
real `FastMCP` server over stdio:

```bash
python -m ceap.mcp.market_data     # stdio MCP server: get_quote, get_quotes, get_order_book, get_trades, get_market_statistics, ...
python -m ceap.mcp.execution       # get_parent_orders, get_child_orders, get_executions, get_execution_metrics, get_venue_statistics, ...
python -m ceap.mcp.risk            # get_position, get_exposure, check_limit, calculate_stress
python -m ceap.mcp.engineering     # search_logs, get_service_metrics, get_deployments, get_latency_metrics, get_services
python -m ceap.mcp.knowledge       # search_documents, get_document, list_documents (RAG)
```

Tool annotations carry `readOnlyHint`, a risk level and required capabilities, which the policy
engine reads. `StdioMCPClient` drives these subprocesses with the official `mcp` SDK; the test
suite includes a real stdio round-trip.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | enables the Anthropic client (`CEAP_LLM_PROVIDER=auto`) |
| `CEAP_LLM_PROVIDER` | `auto` | `auto` / `mock` / `anthropic` |
| `CEAP_LLM_MODEL` | `claude-sonnet-4-5` | narrative / critique model |
| `CEAP_LLM_PLANNING_MODEL` | = model | optional stronger planning model |
| `CEAP_DEFAULT_DATASET` | `T01` | scenario dataset when a request names none |
| `CEAP_STEP_TIMEOUT_SECONDS` | `30` | per tool call |
| `CEAP_TASK_TIMEOUT_SECONDS` | `300` | per investigation |
| `CEAP_MAX_RETRIES` | `2` | transient tool failures |
| `CEAP_AUTO_APPROVE` | `true` | `false` queues approvals for `/approvals` |
| `CEAP_API_KEYS` | dev keys | `key:role,...` (roles: viewer, trader, quant, engineer, admin) |
| `CEAP_KNOWLEDGE_DIR` | `data/reference/knowledge` | RAG corpus |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | – | mirrors harness spans to OpenTelemetry |

---

## Documentation

Site: **https://ashjha0.github.io/AgenticAICashEquities/** (landing page with measured numbers; published from `docs/`, see [docs/GITHUB_PAGES.md](docs/GITHUB_PAGES.md)).

* [LEARN.md](LEARN.md) – guided tour: concepts, how the repo implements them, real numbers, interview questions
* [COOKBOOK.md](COOKBOOK.md) – 27 copy-pasteable recipes
* [docs/SPECIFICATION.md](docs/SPECIFICATION.md) – the governing specification with realised / partial / roadmap status per requirement
* [docs/architecture/overview.md](docs/architecture/overview.md) – components, data flow, design decisions
* [docs/DIAGRAMS.md](docs/DIAGRAMS.md) – Mermaid diagrams: pipeline, state machine, tool-call path, evidence model, MCP topology, sequence, attribution
* [docs/threat-model/threat-model.md](docs/threat-model/threat-model.md) – assets, threats, controls, adversarial tests
* [docs/evaluation/evaluation.md](docs/evaluation/evaluation.md) – scenarios, thresholds, metrics, results
* [docs/api/api.md](docs/api/api.md) – endpoints, auth, schemas
* [docs/INDEX.md](docs/INDEX.md) – everything above in one table

## Roadmap (stage 2)

Research Agent → Alpha Analysis → Backtest Engine → Risk Agent → Critic → Human Approval: the same
harness, policy engine and evidence model extend the platform from execution-quality investigation
into an Agentic Equity Trading Research & Execution Platform. The `HUMAN_APPROVAL` step type and
`QueuedApprovalGateway` already exist for that path.

## License

MIT
