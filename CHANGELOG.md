# Changelog

## 0.3.2 – Fix CI: drop the self-referential lockfile entry

CI has been failing since `requirements.lock` was introduced in 0.2.1 (never verified after
release). `pip-compile` recorded the project itself as a `file:///C:/Work/Claude/...` requirement
- a Windows absolute path that cannot resolve on the Linux runner (`pip install -r
requirements.lock` failed with `No such file or directory: '/C:/Work/...'`). CI's second install
step (`pip install -e . --no-deps`) already installs the project itself, so the self-referential
line in the lockfile was both wrong and redundant; removed it.

Also (unreleased, from local testing): tried deriving pairwise relative-percent-change values for
the narrative number audit to reduce false positives on legitimate arithmetic; reverted after it
let a genuinely fabricated number ("99.9%") pass the audit undetected in the adversarial suite -
the fix made the audit's core fabrication-detection guarantee worse than the noise it removed.

## 0.3.1 – Real-model fixes found by a live Stage 2 evaluation run

Found by running `ceap evaluate --suite research` against `claude-haiku-4-5` for the first time
(prior verification used only the offline deterministic mock).

- `AnthropicLLMClient` sent `temperature`, which `anthropic` >= 1.x no longer accepts on
  `messages.create()`; every "live" call raised and silently fell back to the mock. Removed the
  parameter (sampling controls are gone from the current API; `LLMRequest.temperature` is
  unused).
- The narrative evidence-id audit flagged the approval request id (`APR-...`) as a fabricated
  evidence reference — it was never meant to resolve as `Evidence`. The audit regex now only
  matches ids shaped like an actual evidence prefix (`ceap.domain.evidence._PREFIX`).
- The narrative number audit didn't recognise bps/per-mille scalings a reporter model legitimately
  uses (e.g. quoting a fraction as bps); extended the derived-value set with `x1000`, `x10000` and
  their inverses.
- A real (non-mock) planner occasionally produced an incomplete research plan under
  `CEAP_LLM_MODEL=claude-haiku-4-5` alone (5/21 scenarios landed on `INCOMPLETE_ANALYSIS`).
  Setting `CEAP_LLM_PLANNING_MODEL=claude-sonnet-5` fixed it: 21/21 verdict accuracy and flag
  coverage on a second live run, with false-flag rate down from 0.33 to 0.10.

## 0.3.0 – Stage 2: Research → Alpha → Backtest → Risk → Critic → Human Approval

The platform now runs a second workflow on the same harness, policy engine, evidence model and
approval gateway: evaluate a trading signal on a research universe and propose whether to promote it,
with a human approval gate and an optional, separately approved paper order-staging step.

### Data and analytics
- `ceap.data.historical`: a seeded 30-name, four-year daily history generator (factor model with a
  scenario-controlled embedded premium computed from the same signal code the analytics use);
  `HistoricalStore` mirrors `DatasetStore`. Liquidity tiers, spreads and ADV are per name.
- `ceap.data.research_scenarios`: seven templates × three seeds = 21 scenarios with structured
  ground truth (`expected_verdict`, `expected_flags`): momentum premium, no alpha, reversal premium,
  regime break, cost drag, concentration, mixed.
- `ceap.analytics.signals` (momentum_12_1, reversal_5, low_vol_60), `signal_statistics` (rank IC,
  t-stat, IR, hit rate, turnover, decay, quantile spread - one-day ICs for honest t-stats),
  `backtest` (event-driven daily loop, half-spread + square-root impact cost model, walk-forward
  split, per-period statistics, P&L concentration), `portfolio_risk` (recomputed target portfolio,
  limits, stress) and `research_assessment` (the deterministic verdict and flags).
- `ceap.domain.research`: scope, statistics, backtest, risk and assessment objects plus
  `research_tool_arguments` - the single source of tool arguments for the plan, the agents and the
  harness tail, so no tool is invoked twice. `EvidenceType.SIGNAL` / `BACKTEST`.

### MCP servers
- New `research_data`, `alpha` and `backtest` servers; `risk` gains `get_portfolio_exposure`,
  `check_portfolio_limits`, `calculate_portfolio_stress` (they recompute the portfolio from
  `(signal, dataset, as_of)` and never trust backtest output); `execution` gains the only
  non-read-only tool, `stage_orders` (HIGH risk, requires `trading:execute`; idempotent paper
  orders held in memory) and `get_staged_orders`. All run in-process or over stdio.

### Harness, policy, agents
- `Task.input["kind"]` selects the workflow. The governance tail is harness-owned for both kinds
  and, for research, is critic → validation → **proposal approval** → [**staging approval** →
  `stage_orders`] → finalise; plan-supplied approval steps are stripped, a declined proposal fails
  the task, a declined staging approval skips staging and the report says so. The approver sees the
  critic's assessment and the recomputed target portfolio (or the order preview).
- Plan validation pins `signal`, accepts any symbol of the task universe, and pins `in_sample_end` /
  `as_of` to the task's dates. The planner only ever shows the model read-only tools.
- Agents `research`, `alpha`, `backtest`, `portfolio_risk`; the critic computes the deterministic
  research assessment and caps findings that contradict it; the reporter writes the proposal
  (HYPOTHESIS, SIGNAL STATISTICS, BACKTEST, RISK, VERDICT, APPROVAL, STAGED ORDERS) with the same
  number and evidence-id audits; `InvestigationReport.kind` / `proposal`.
- Capabilities: `research` (quant, admin), `research:read` (all roles), `trading:execute` (admin).
  Requesting order staging without `tools:write` + `trading:execute` is refused up front.

### API, CLI, evaluation
- `POST /research`, `GET /research[/{id}[/report|/result|/trace]]`, `POST /research/{id}/cancel`,
  `GET /research-scenarios`; `ReportOut.kind` / `proposal`; approvals endpoints unchanged.
- `ceap research "…" [--signal --dataset --stage-orders --role admin]`, `ceap research-scenarios`,
  `ceap evaluate --suite investigation|research|all`.
- 21-scenario research evaluation (verdict accuracy, flag coverage, false flags, approvals) next to
  the 50-scenario execution evaluation; new unit, MCP, agent, adversarial and API suites.

## 0.2.1 – CI, dependency pinning, test organisation

- Add GitHub Actions CI (`ruff`, `mypy`, `pytest`) running against a pinned `requirements.lock`.
- Pin `mcp>=1.19,<2` (the code uses the v1 `FastMCP` API, renamed in `mcp` 2.x) and generate
  `requirements.lock` via pip-compile for reproducible installs.
- `Settings.validate_for_serving()` now logs a warning when serving with dev API keys /
  auto-approve; `CEAP_API_KEYS` rejects duplicate key entries; add `.env.production.example`.
- Fix 13 latent `mypy` errors in the analytics layer surfaced by a newer `numpy` (widen
  `Sequence[float]` parameters to also accept `np.ndarray`; narrow `_round_tick`'s return type).
- Split `tests/unit/test_analytics.py` into per-metric files under `tests/unit/analytics/` with a
  few added edge-case tests (145 tests total; ruff and mypy clean).

## 0.2.0 – Review pass: hardening, correctness, bounded resources

Findings from a full code review (three independent reviewers plus `mypy --strict`-style typing),
each fixed with a regression test in `tests/agent/test_review_regressions.py`.

### Harness and governance
- Governance steps (critic → validation → finalise) are always appended in canonical order; a plan
  that places the critic before the specialists, or omits validation, is corrected and the change
  is recorded as a warning. A critic is forced before finalisation whenever agents ran after it.
- Plan validation pins `symbol` and `dataset` to the task and only accepts window arguments equal
  to the task boundaries; caps of 64 steps, 48 tool calls and 8 KB per argument set; only scalar,
  JSON-serialisable arguments are accepted. Policy-engine exceptions surface as plan errors.
- Cancellation during a concurrent tool batch now yields `CANCELLED` rather than `FAILED`
  (`TaskCancelled` unwrapped from `BaseExceptionGroup`). Per-attempt timeouts are bounded by the
  remaining task deadline; approvals race against cancellation; a crashing policy engine denies.
- Concurrent `REQUIRE_APPROVAL` steps use a counter so the state machine does not flap.
- `TransientToolError` (connection/OS/timeout errors from MCP transports) is the only retryable class.

### Agents and LLM
- Planner sanitises arguments against the tool schema (drops unknown keys, wrong types and non-
  scalars; re-pins symbol/dataset) and reports `rejected_steps`; planning facts are passed as
  request metadata so `{}` in the user question can no longer hijack the mock planner.
- Agents only reuse a cached tool output when the arguments match exactly.
- Critic tolerates an LLM failure; NaN confidence proposals are ignored; reporter, critic and
  planner surface `llm_fallback` and the router records `fallback_reason`.
- Execution/engineering agents format `None`/non-finite values safely; deployment timestamps are
  compared as datetimes rather than ISO strings.
- Prompts frame retrieved documents and tool outputs as data, not instructions.

### Analytics and MCP servers
- `venue_volume` sums per venue (previously kept only the last print).
- Post-trade impact is measured at last fill + horizon; opportunity cost pro-rated for sub-horizon
  windows; quote index returns `None` before the first quote; mids use `[start, end)`.
- Attribution is `None`/NaN safe; deterioration uses only finite deltas; peer means ignore NaN.
- `downsample` keeps the first and last points; `get_order_book` returns the book at the exact
  requested timestamp; `check_limit` accepts `side` and uses the current position.
- Optional parameters export as `anyOf` with `null` in MCP schemas; stdio servers run with the
  current interpreter and forward `PYTHONPATH`/virtualenv variables; session access is serialised.

### API, configuration and observability
- Development API keys and auto-approval apply only when `CEAP_ENV=dev`; `validate_for_serving()`
  refuses to start otherwise. Numeric/boolean settings are validated; keys must be ≥ 8 characters.
- Background investigations run under a semaphore (`CEAP_MAX_CONCURRENT_INVESTIGATIONS`), record
  failures instead of vanishing, and are cancelled on shutdown; retained results are bounded
  (`CEAP_MAX_RETAINED_RESULTS`); cancel requires the owner or `approvals:decide`.
- Constant-time API-key lookup; principals are logged as a key digest. Approving a decided request
  is a 409. Malformed `session_date` is a 422.
- NaN/Infinity are scrubbed at the JSON boundary (`to_jsonable`, CLI `allow_nan=False`).
- Metrics registry bounded to 500 label sets per metric with escaped labels; tracer uses a
  contextvar (correct parents under concurrency) and caps retained spans; mock LLM call log and
  approval logs are bounded.
- Question parser: first-mentioned symbol wins; clock-like ranges beat numeric ranges with units.
- Dependencies: `mcp>=1.19`, `tzdata` for Windows time zones.
- 138 tests (22 new regressions); ruff and mypy clean; evaluation unchanged at 50/50.

## 0.1.0 – MVP: Execution Quality Investigation

- Domain model (tasks, plans, tools, evidence, findings, market/execution objects, policy).
- Deterministic analytics: VWAP, TWAP, implementation shortfall, slippage, spreads, market impact,
  liquidity, volatility, market statistics, TCA bundle with venue statistics, cause attribution.
- Synthetic cash-equity market model with 10 scenario templates x 5 symbols (50 scenarios) and
  structured ground truth.
- Five MCP servers (market data, execution, risk, engineering, knowledge) usable in-process or over
  stdio via FastMCP; MCP client abstraction, tool adapter with evidence digests, registry discovery.
- Agent harness: explicit state machine, policy-gated executor with approvals, retry, timeout,
  cancellation, memory, tracing, concurrent tool batches, enforced governance steps.
- Agents: planner (LLM, sanitised), market, execution, quant, risk, engineering, critic
  (deterministic + LLM that can only lower confidence), reporter with narrative number/evidence audits.
- Policy engine with RBAC, argument guards, symbol universe, risk-level approvals.
- RAG knowledge base over runbooks/policies exposed as an MCP server.
- FastAPI gateway with API-key auth and capability checks; CLI; Prometheus metrics; JSON logging;
  OpenTelemetry-ready tracing.
- 116 tests: unit, integration, MCP (incl. real stdio round-trip), agent, adversarial, 50-scenario evaluation.
