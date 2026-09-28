# Specification — Agentic AI Cash Equities Platform

The governing specification for the platform: what it must do, the architectural rules it
must obey, the interfaces it must expose, and — for each requirement — where the repository
realises it. Requirements are numbered `S-n`; every one has a status of **realised**,
**partial** or **roadmap**, and realised items name the module and the test that pins them.

Contents: 1. [Objective](#1-objective) · 2. [Architecture](#2-architecture) · 3. [Architectural rules](#3-architectural-rules) ·
4. [MVP use case](#4-mvp-use-case) · 5. [Domain interfaces](#5-domain-interfaces) · 6. [Cash-equity domain objects](#6-cash-equity-domain-objects) ·
7. [Analytics](#7-analytics) · 8. [MCP servers](#8-mcp-servers) · 9. [Agent harness](#9-agent-harness) · 10. [Agents](#10-agents) ·
11. [Policy and governance](#11-policy-and-governance) · 12. [RAG](#12-rag) · 13. [Evaluation](#13-evaluation) · 14. [API](#14-api) ·
15. [Observability](#15-observability) · 16. [Technology stack](#16-technology-stack) · 17. [Repository layout](#17-repository-layout) ·
18. [Delivery phases](#18-delivery-phases) · 19. [Report format](#19-report-format) · 20. [Stage 2](#20-stage-2)

## 1. Objective

**S-1.** Build an enterprise-style Agentic AI platform that can investigate, analyse and
explain cash-equity trading and market behaviour. The MVP must answer questions such as:

- "Why did our execution quality for AAPL deteriorate between 14:00 and 15:00 yesterday?"
- "Analyse today's VWAP execution for MSFT and identify the main sources of slippage."
- "Investigate whether the deterioration was caused by market conditions, venue liquidity,
  our execution algorithm, or technology latency."

*Status: realised* — `ceap.cli`, `ceap.api`, `ceap.platform`; `tests/agent/test_harness.py`.

**S-2.** The platform combines Python, MCP, LLMs, agentic AI, an agent harness, market data,
execution data, quant analytics, RAG, risk, observability and governance.
*Status: realised* — see §16.

## 2. Architecture

**S-3.** Target architecture:

```
User (trader / quant / dev)
  └─ API / Gateway (FastAPI + auth/RBAC)
       └─ AGENT HARNESS (planner · state machine · tool orchestration · policy · memory ·
                         retry/timeout · approval · observability)
            ├─ Market agent · Execution agent · Risk agent (+ Quant, Engineering, Critic)
            └─ MCP gateway
                 ├─ Market Data MCP   (quotes/trades · order book · reference data)
                 ├─ Execution MCP     (orders/fills · TCA · positions)
                 ├─ Risk MCP          (positions · exposure · limits · stress)
                 └─ Engineering MCP   (logs · metrics · deployments · traces)
                      └─ Quant Analytics (VWAP · TWAP · IS · slippage · volatility · liquidity · impact)
```

*Status: realised* — `docs/architecture/overview.md`, `docs/DIAGRAMS.md` §1. A fifth
server, `knowledge`, was added for RAG (§12).

**S-4.** The modelled chain is Exchange/Venue → Market Data → Order Book → Execution
Strategy → Order → Venue → Execution → TCA / Risk / P&L.
*Status: realised* — `ceap.data.synthetic` generates every stage; `ceap.analytics` measures it.

## 3. Architectural rules

**S-5.** *LLM = reasoning and orchestration assistance; Python analytics = deterministic
computation; MCP = capability interface; harness = control plane; policy engine = authority;
evidence = auditability.*

**S-6.** The LLM must never be responsible for calculating VWAP, implementation shortfall,
slippage or market impact when Python can calculate them exactly.
*Status: realised* — metrics are computed in `ceap.analytics` inside the execution MCP server;
the reporter receives them as facts; `audit_numbers` flags any narrative number not traceable
to those facts (`tests/adversarial::test_fabricated_numbers_and_evidence_ids_are_flagged`).

**S-7.** The harness controls the agent rather than allowing the agent to control itself.
*Status: realised* — `AgentHarness` state machine; agents receive a `ToolInvoker`, never the
registry; governance steps enforced (`test_harness_enforces_governance_steps`).

**S-8.** The harness is the project's own domain abstraction, not tightly coupled to a
particular agent framework.
*Status: realised* — `ceap.harness` is pure Python/asyncio over `ceap.domain` interfaces.

**S-9.** Domain interfaces are independent of MCP and LLM frameworks.
*Status: realised* — `ceap.domain` imports nothing from `mcp`, `anthropic` or `fastapi`
(`docs/DIAGRAMS.md` §8).

## 4. MVP use case

**S-10.** Execution Quality Investigation. Given "Analyse our AAPL execution between 14:00
and 15:00 and explain why implementation shortfall increased", the system performs:

| Step | Realised by |
|---|---|
| 1 Understand request | `ceap.api.parsing.parse_question` + `Platform.validate_request` |
| 2 Build investigation plan | `PlannerAgent` (LLM) → `Plan` |
| 3 Retrieve orders | `execution.get_parent_orders`, `get_child_orders` |
| 4 Retrieve executions | `execution.get_executions` |
| 5 Retrieve market data | `market_data.get_market_statistics` (window + baseline) |
| 6 Retrieve order-book information | `market_data.get_order_book_statistics` (window + baseline) |
| 7 Calculate VWAP, arrival, IS, spread, volatility, participation, fill rate | `execution.get_execution_metrics` → `StandardExecutionAnalytics` |
| 8 Analyse venue behaviour | `execution.get_venue_statistics` + `ExecutionAgent` peer comparison |
| 9 Analyse execution algorithm | `execution.get_strategy_configuration` + runbook comparison |
| 10 Check technology metrics | `engineering.get_latency_metrics`, `get_deployments`, `search_logs` → `EngineeringAgent` |
| 11 Generate candidate explanations | `QuantAgent` → `attribute_causes` |
| 12 Critic agent challenges findings | `CriticAgent` |
| 13 Validate evidence | harness `VALIDATING_EVIDENCE` |
| 14 Produce report | `ReportAgent` → `InvestigationReport` |

*Status: realised* — `tests/agent/test_harness.py::test_full_investigation_normal_scenario`.

## 5. Domain interfaces

**S-11.** The following types exist with the stated shape (`ceap.domain`):
`Task`, `Plan`, `PlanStep`, `StepType` (ANALYSIS, TOOL_CALL, AGENT_CALL, VALIDATION,
HUMAN_APPROVAL, FINALISE), `Agent` (id, type, `execute(context)`), `AgentContext` (task_id,
execution_id, deadline, task_input, current_plan, evidence, state, tool_registry,
policy_context, cancellation_event), `Tool` (id, metadata, `execute(request, context)`),
`ToolMetadata` (id, name, description, read_only, risk_level, required_capabilities),
`ToolRequest` (tool_id, arguments, correlation_id), `ToolResult` (status, data, evidence,
error, execution_time_ms), `MCPClient` (`discover_tools`, `invoke`), `LLMClient`
(`complete`), `LLMRequest`, `LLMResponse`, `EvidenceType` (MARKET_DATA, ORDER_DATA,
EXECUTION_DATA, ORDER_BOOK, SYSTEM_METRIC, LOG, DOCUMENT, CALCULATION, CODE_CHANGE),
`Evidence`, `Finding` (statement, supporting_evidence, contradicting_evidence, confidence),
`PolicyDecision` (ALLOW, DENY, REQUIRE_APPROVAL), `PolicyEngine`, `MarketDataRepository`,
`ExecutionRepository`, `ExecutionAnalytics`.
*Status: realised* — `tests/unit/test_domain.py`. Additions beyond the brief: `ToolInvoker`,
`AgentResult`, `RiskLevel`, `EvidenceType.RISK`, `InvestigationReport`.

**S-12.** `Tool → MCPToolAdapter → MCPClient → MCP Server` layering.
*Status: realised* — `ceap.mcp.adapter`, `ceap.mcp.client`; `tests/mcp/test_servers.py`.

## 6. Cash-equity domain objects

**S-13.** `Quote` (symbol, timestamp, bid, bid_size, ask, ask_size, last_price, last_size,
volume), `Trade` (symbol, timestamp, price, quantity, venue), `OrderBookLevel` /
`OrderBookSnapshot` (side, price, quantity, level), `Order` (order_id, parent_order_id,
symbol, side, quantity, limit_price, strategy, venue, timestamp), `Execution` (execution_id,
order_id, symbol, side, quantity, price, venue, timestamp), `ExecutionMetrics` (fill_rate,
participation_rate, vwap, arrival_price, implementation_shortfall_bps, slippage_bps,
average_spread_bps, market_impact_bps, average_latency_us).
*Status: realised* — `ceap.domain.market`, `ceap.domain.execution`; `ExecutionMetrics` also
carries market VWAP, both slippages, effective spread, temporary impact, drift, volatility,
reject rate and per-venue statistics.

**S-14.** Securities: AAPL, MSFT, NVDA, AMZN, META, GOOGL.
*Status: realised* — `ceap.data.scenarios.SYMBOLS`.

## 7. Analytics

**S-15.** Deterministic Python analytics for VWAP, implementation shortfall (executed cost −
decision price × target quantity, normalised to bps), slippage (execution − benchmark), fill
rate, participation rate, average spread, realised spread, market impact, execution latency,
venue fill rate, venue slippage, price drift, volatility.
*Status: realised* — `ceap.analytics.*`; `tests/unit/test_analytics.py` (known-answer tests
including sign conventions and opportunity cost). "Realised spread" is delivered as effective
spread plus temporary impact.

**S-16.** Attribution of candidate causes (market conditions, venue liquidity, execution
algorithm, technology latency, market-data anomaly, large order, price movement).
*Status: realised* — `ceap.analytics.attribution`; thresholds documented in
`docs/evaluation/evaluation.md`.

## 8. MCP servers

**S-17.** Market Data MCP: `get_quote(symbol, timestamp)`, `get_order_book(symbol,
timestamp)`, `get_trades(symbol, start, end)`, `get_market_statistics(symbol, start, end)`.
*Status: realised* (+ `get_quotes`, `get_order_book_statistics`, `get_reference_data`,
`get_coverage`).

**S-18.** Execution MCP: `get_parent_orders`, `get_child_orders`, `get_executions`,
`get_execution_metrics`, `get_venue_statistics`.
*Status: realised* (+ `get_strategy_configuration`).

**S-19.** Risk MCP: `get_position`, `get_exposure`, `check_limit`, `calculate_stress`.
*Status: realised* — `calculate_stress` is MEDIUM risk and exercises the approval path.

**S-20.** Engineering MCP: `search_logs`, `get_service_metrics`, `get_deployments`,
`get_latency_metrics`.
*Status: realised* (+ `get_services`).

**S-21.** Servers are real MCP servers (Python MCP implementation) as well as in-process
callable.
*Status: realised* — `MCPServerDefinition.to_fastmcp()` / `run_stdio()`, `StdioMCPClient`;
`tests/mcp::test_stdio_transport_round_trip`, `scripts/run_stdio_mcp_demo.py`.

## 9. Agent harness

**S-22.** Task → Planner → Plan validation → Step execution → Tool invocation → Evidence
collection → Validation → Critic → Final response, as a state machine using Python's
asynchronous execution model (`async def execute_plan(plan) -> AgentResult`, asyncio,
TaskGroup).
*Status: realised* — `ceap.harness.engine`, `state_machine`, `executor`, `retry`,
`cancellation`, `memory`; `tests/unit/test_harness_primitives.py`,
`tests/agent/test_harness.py`.

**S-23.** Retry, timeout, cancellation, approval and observability are harness concerns.
*Status: realised* — `RetryPolicy`/`retry_async`, per-step timeout bounded by the task
deadline, `CancellationToken`, `ApprovalGateway`, `ExecutionTracer` + metrics.

## 10. Agents

**S-24.** Planner (natural-language task → ordered plan converted into typed `PlanStep`s);
Market (what happened in the market?); Execution (what happened to our orders?); Quant (what
does the data statistically show?); Risk (unusual exposure or risk?); Engineering (did our
technology behave normally?); Critic (are these conclusions actually supported?).
*Status: realised* — `ceap.agents.*`; plus `ReportAgent`.

## 11. Policy and governance

**S-25.** A policy engine evaluates every tool request (ALLOW / DENY / REQUIRE_APPROVAL)
with RBAC and human approval.
*Status: realised* — `ceap.policy.engine.RulePolicyEngine`, `permissions`, `approvals`;
`tests/unit/test_policy.py`, approval flow tests.

**S-26.** Findings must cite evidence; conclusions must be auditable.
*Status: realised* — `Finding` requires evidence; `VALIDATING_EVIDENCE` drops unresolved
findings; critic assessments and narrative audits attached to the report.

## 12. RAG

**S-27.** Enterprise knowledge (execution algorithm documentation, VWAP runbook, TCA
methodology, venue configuration, trading limits, market data architecture, incident
runbooks, execution policy) retrievable so the agent can compare configuration/policy with
actual execution data ("Was this behaviour consistent with our VWAP configuration?").
*Status: realised* — `data/reference/knowledge/*.md`, `ceap.rag`, `knowledge` MCP server;
the execution agent compares observed participation with the configured ceiling and cites
the runbook; `tests/unit/test_rag_and_parsing.py`.

## 13. Evaluation

**S-28.** 50 test scenarios (normal VWAP, high volatility, wide spreads, low liquidity, venue
degradation, technology latency, market-data anomaly, large parent order, unexpected price
movement, mixed market + technology) with expected results as structured ground truth.
*Status: realised* — `ceap.data.scenarios` (10 templates × 5 symbols), `ceap.evaluation`,
`tests/evaluation/test_scenarios.py`; results in `docs/evaluation/evaluation.md`.

**S-29.** Adversarial tests.
*Status: realised* — `tests/adversarial/test_adversarial.py`, mapped to controls in
`docs/threat-model/threat-model.md`.

## 14. API

**S-30.** FastAPI + Pydantic + Uvicorn gateway with auth/RBAC.
*Status: realised* — `ceap.api` (API keys → roles → capabilities); `docs/api/api.md`;
`tests/integration/test_api.py`.

## 15. Observability

**S-31.** OpenTelemetry, Prometheus, Grafana.
*Status: partial* — in-memory span tracer with optional OpenTelemetry mirroring,
Prometheus text exposition at `/metrics`, JSON-lines logs. Grafana dashboards are not
shipped (roadmap).

## 16. Technology stack

| Area | Specified | Realised |
|---|---|---|
| API | FastAPI, Pydantic, Uvicorn | yes |
| Harness | pure Python, asyncio, TaskGroup, dataclasses, typing | yes |
| MCP | Python MCP implementation | `mcp` SDK (FastMCP + stdio client); in-process fast path |
| Quant | NumPy, Pandas, Polars, SciPy, PyArrow, DuckDB | NumPy + Pandas core; Polars/PyArrow/DuckDB/SciPy as the `quant` extra (CSV/Parquet export) |
| ML | scikit-learn, XGBoost, PyTorch — only where deterministic analysis is insufficient | not needed for the MVP; `ml` extra reserved |
| Storage | PostgreSQL, Parquet, DuckDB, object storage, vector DB | in-memory repositories + Parquet/CSV export; hashed TF-IDF vector index (dependency-free) — roadmap for Postgres/vector DB |
| LLM | provider-agnostic | `LLMClient` with Anthropic adapter and deterministic mock; router with fallback |
| Observability | OpenTelemetry, Prometheus, Grafana | OTel-optional tracer, Prometheus text metrics; Grafana roadmap |

## 17. Repository layout

The specified layout (`src/domain, harness, agents, mcp, llm, analytics, rag, policy, api`;
`tests/unit, integration, agent, mcp, evaluation, adversarial`; `data/market, orders,
executions, reference`; `docs/architecture, threat-model, api, evaluation`) is realised under
a single importable package `src/ceap/` — a bare top-level `mcp` package would shadow the
`mcp` SDK. `ceap.data` (synthetic model) and `ceap.observability` are additions.

## 18. Delivery phases

| Phase | Scope | Status |
|---|---|---|
| 0 Domain model | Task, Plan, PlanStep, Agent, Tool, ToolRequest, ToolResult, Evidence, Finding, Execution, Order, MarketSnapshot, ExecutionMetrics, PolicyDecision | realised |
| 1 Market model | synthetic but realistic data for six symbols: quotes, order book, orders, executions | realised |
| 2 Execution analytics | VWAP, IS, slippage and the additional metrics | realised |
| 3 MCP servers | four servers | realised (+ knowledge) |
| 4 Agent harness | state machine, async execution | realised |
| 5 Planner agent | LLM plan → typed steps | realised |
| 6 Multi-agent analysis | market, execution, quant, risk, engineering, critic | realised |
| 7 RAG | enterprise documents | realised |
| 8 Evaluation | 50 scenarios with ground truth | realised |

## 19. Report format

**S-32.** The final report has the sections EXECUTIVE SUMMARY, PRIMARY OBSERVATIONS,
EXECUTION (VWAP, arrival price, IS, spread, fill rate), MARKET CONDITIONS (volatility,
displayed depth), TECHNOLOGY (latency, reject rate), CONCLUSION, ALTERNATIVE EXPLANATIONS and
EVIDENCE (ids), with the LLM writing the explanation and the numbers originating from
deterministic systems.
*Status: realised* — `ceap.agents.reporter`, `ceap.llm.prompts.REPORT_SYSTEM_PROMPT`, the
deterministic narrative template in `ceap.llm.client`; POLICY CONTEXT and CRITIC sections added.

## 20. Stage 2

**S-33.** Research Agent → Alpha Analysis → Backtest Engine → Risk Agent → Critic Agent →
Human Approval, evolving the MVP into an Agentic Equity Trading Research & Execution
Platform while keeping the architectural boundary.
*Status: realised (0.3.0)* — `ceap.agents.{research,alpha,backtest,portfolio_risk}`, the
`research_data` / `alpha` / `backtest` MCP servers and the portfolio tools on `risk`;
`ceap.analytics.{signals,signal_statistics,backtest,portfolio_risk,research_assessment}`;
`ceap.data.{historical,research_scenarios}`. The harness appends the governance tail
critic → validation → `HUMAN_APPROVAL` (the proposal, decided through `QueuedApprovalGateway`) and,
when order staging is requested, a second `HUMAN_APPROVAL` followed by the one non-read-only tool,
`execution.stage_orders` (HIGH risk, `trading:execute`, paper orders only). Pinned by
`tests/agent/test_research_harness.py`, `tests/adversarial/test_research_adversarial.py`,
`tests/integration/test_research_api.py` and the 21-scenario `tests/evaluation/test_research_scenarios.py`.

| Phase | Scope | Status |
|---|---|---|
| 9 Research data & analytics | historical generator, research scenarios with ground truth, signals, IC statistics, backtest engine, portfolio risk, assessment | realised |
| 10 Research workflow | research/alpha/backtest/portfolio-risk agents, critic assessment, proposal report, two approval gates, paper order staging, API/CLI, evaluation | realised |
