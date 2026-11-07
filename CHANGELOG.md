# Changelog

Every pull request adds a line under `## Unreleased` (CI enforces it unless the PR is labelled
`no-changelog`). At release time the section is renamed `## [X.Y.Z] - date`; the release gate
refuses a tag whose version has no section.

## Unreleased

- R5 5.1: preregistration of the 12-1 momentum study (`docs/research/prereg_momentum_2026-11.md`), second-read and committed with its SHA-256 in `docs/research/prereg_lock.json` before any price data was read; every price loader refuses to run on a hash mismatch or past 2023-12-31 (the pseudo-holdout).
- R5 5.2: approximately point-in-time S&P 500 membership (`ceap.data.real.universe_pit`, `data/reference/sp500_changes.csv` from fja05680/sp500 reconciled against Wikipedia, CC BY-SA attribution in `data/reference/ATTRIBUTION.md`), dated ticker map and a monthly coverage report.
- R5 5.3: Stooq and yfinance daily loaders reading a gitignored local cache (`data/real_cache/`, never committed), `scripts/download_daily.py` (yfinance; Stooq now serves a browser check to scripts and is not bypassed), and a data quality gate with planted-defect tests.
- R5 5.4: real-data cost schedule `ceap.analytics.real_costs` (5/10 bps half-spread, square-root impact Y = 0.7 at USD 100M, borrow GC plus hard-to-borrow, Corwin-Schultz as sensitivity) with known-answer tests.
- R5 5.5: UMD replication gate `ceap.analytics.factor_check` (correlation >= 0.5 or PIPELINE SUSPECT), FF5 + UMD loadings, per-year and crash-month tables, breakeven cost; `data/reference/ff_factors.csv` from the Ken French library.

## [0.4.0] - 2026-11-07

Rounds R0, R1 and R2 of the 2026-11-07 plan, shipped as one combined PR (AshJha0/AgenticAICashEquities#1).
CI green on the combined head e915c8a: run 42396076903 (all 9 jobs).

Round R0 of the 2026-11-07 plan (`docs/review/2026-11-07/PLAN.md`): foundations for 0.4.0.

### Breaking / migration

- `requirements.lock` is now a universal, hashed lock produced by `uv pip compile --universal
  --generate-hashes`. Install it with `pip install --require-hashes -r requirements.lock` (the new
  `scripts/bootstrap.sh` and `scripts/bootstrap.ps1` do this). The old instructions to hand-edit the
  lock after regenerating it are gone.
- The `ml` extra no longer pulls `xgboost` (it was never imported).
- The `platform` test fixture is function-scoped; tests that relied on state left by an earlier
  test must create it themselves.
- `Task.create` redacts emails, API keys, tokens and account-like numbers from the question text.

### Added

- Lock refreshed for pip-audit findings: certifi 2026.10.23 (PYSEC-2026-4320) and
  opentelemetry-api/sdk 1.45.2 (CVE-2026-114603). `.gitleaks.toml` extends the default rules and
  allowlists only the documented `dev-*-key` demo keys.

- `src/ceap/util/canonical.py`: canonical bytes for every hash (LF, sorted-key JSON, UTC datetimes
  in one format, NaN refused). `.gitattributes` makes every text file LF in the repository.
- `src/ceap/util/redact.py`: redaction for question text and log lines, and a cassette scrubber
  that removes `x-api-key`, request ids and organisation ids.
- Domain contracts frozen for later rounds: `TaskStatus` (with `DEGRADED`, `INCOMPLETE_ANALYSIS`,
  `INTERRUPTED`), `ResultWarning`, `EvidenceRef` and `Finding.evidence_refs`, `HypothesisTable`,
  `LLMCallRecord`, `schema_version` on `ToolResult` and `InvestigationReport`, and
  `ApprovalRequest.content_hash`. All additive with defaults.
- CI: least-privilege permissions, a concurrency group, actions pinned to commit SHAs, pip cache,
  a Python 3.11 and 3.12 matrix, pytest-xdist, a coverage floor of 80 %, `lock-fresh`, `pip-audit`,
  gitleaks with a planted-key self-test, a Docker build and `ceap --help` smoke test, a CHANGELOG
  check and a domain-contracts check (`scripts/check_contracts.py`, label `contracts-change`).
- `nightly.yml`: every test tier in random order with 1000 hypothesis examples per property.
- `release.yml` and `scripts/release_check.py`: a tag releases only when every check on the commit
  is green, the version agrees in `pyproject.toml`, `ceap.__version__` and the tag, the CHANGELOG
  has the section, `scripts/check_banned_words.py` is clean and every dependency licence is on the
  allowlist.
- Test markers `slow` and `nightly`; hypothesis profiles (`pr` 100 examples, `nightly` 1000, both
  derandomised).

### Changed

- COOKBOOK (recipe 34), the architecture overview and the diagrams page (section 10, CI and the release gate) describe the
  new workflow.

### R1: fail closed by default (PLAN section 2, items 1.1-1.11)

#### Breaking / migration

- **`CEAP_LLM_PROVIDER=auto` is removed.** It silently used the mock when no key was set. Set
  `CEAP_LLM_PROVIDER=anthropic` (needs the `llm` extra and `ANTHROPIC_API_KEY`) or
  `CEAP_LLM_PROVIDER=mock`. `auto` now fails at startup with a message naming both. Outside
  `CEAP_ENV=dev` the variable must be set explicitly; in dev it defaults to `mock`.
- `CEAP_LLM_PROVIDER=anthropic` with a client that cannot be built (no SDK, bad key) raises
  `SettingsError` at startup instead of falling back to the mock.
- Model ids must be pinned in `src/ceap/llm/model_pins.toml`: `claude-opus-5-5` (planning),
  `claude-sonnet-5-5` (narrative, critique), `claude-haiku-5-5` (reserved for judge paths). The
  old default `claude-sonnet-4-5` (retires 2026-11-30) and any unpinned id are refused.
- `LLMRequest.temperature` is removed; `max_tokens` defaults to the pinned value per purpose
  (planner 16000, critic 8000, reporter 8000) to leave room for adaptive thinking.
- Four-eyes approvals: new `approver` role holds `approvals:decide`; `admin` loses it (admins
  keep cancel-any through the new `tasks:cancel-any` capability). Nobody may decide an approval
  they requested (403), and the staging approval must come from a different principal than the
  proposal approval. Dev keys `dev-approver-key` and `dev-approver2-key` were added.
- `AgentHarness()` without an approval gateway now denies every approval (was: auto-approve).
  `CEAP_AUTO_APPROVE=true` outside `CEAP_ENV=dev` refuses to serve.
- `HarnessResult.warnings` is now `list[ResultWarning]` (code, message, source, severity), not
  `list[str]`; use `result.warning_messages()` for the old shape. The API status field now reports
  `COMPLETED`, `DEGRADED`, `INCOMPLETE_ANALYSIS`, `FAILED` or `CANCELLED`, and `to_dict()` adds
  `status`, `banner` and `incomplete_agents`.
- MCP trust fails closed: a missing `readOnlyHint` means not read-only and a missing or unknown
  `riskLevel` means HIGH (in-process adapter and stdio client). Tools of a server outside
  `CEAP_MCP_TRUSTED_SERVERS` (default: the eight built-in servers) cannot write. A tool result
  whose `schema_version` differs from `SCHEMA_VERSION` is rejected.
- Standalone stdio MCP servers enforce `required_capabilities` for the principal named by
  `CEAP_MCP_PRINCIPAL_ROLES`; unset means no capabilities, so `stage_orders` is refused.

#### Added

- Degradation ladder, visible on every result: rung 1 refusal answered by the pinned fallback
  model, rung 2 one structured-output repair retry, rung 3 canonical plan / deterministic
  critique / templated narrative, rung 4 `CEAP_LLM_DISABLED=true` kill switch with a banner. Each
  rung above 0 adds a `ResultWarning` and sets `DEGRADED`; `stop_reason` `max_tokens` and
  `refusal` are recorded with `stop_details`.
- Required specialists: the harness appends market, execution, quant, risk or engineering if an
  investigation plan omits one. A failed required agent yields `INCOMPLETE_ANALYSIS` and a report
  that says so instead of "no material anomaly"; a timed-out agent returns its checkpointed
  `PARTIAL` output.
- Model calls: per-call timeout `CEAP_LLM_TIMEOUT_S` (60 s), raced against cancellation,
  concurrency cap `CEAP_LLM_MAX_CONCURRENCY` (4) and per-task cap `CEAP_LLM_MAX_CALLS_PER_TASK`
  (12). The client retries 408/409/429/5xx and transport faults, never other 4xx. Adaptive
  thinking and the pinned `effort` are sent on the wire.
- Dockerfile: Python 3.12, digest-pinned base, dependencies (with `[llm]`) only from the hashed
  lock, non-root user, `HEALTHCHECK`; new `.dockerignore`.
- README banner (synthetic data, nothing trades, paper orders only, no live model numbers);
  threat model T17 corrected and T20-T22 added; 39 tests named `test_1_N_*` in
  `tests/agent/test_r1_fail_closed.py`.

#### Changed

- The job-prep word is gone from every tracked file; LEARN section 17 is now "Knowledge check".
  `Claude outputs/ci.yml` (a stale copy of the workflow) is deleted.
- CI run id for this round: 42396076903 (combined head e915c8a).

### Integration (combined 0.4.0 PR: R0 + R1 + R2)

- R2 shipped in 0.4.0 rather than 0.5.0 at the owner's request for one combined PR; version labels
  in code and docs say 0.4.0.
- `reporter` and `critic` now test `ResearchFlag.OOS_DECAY` (they still compared the literal
  `"OVERFIT"`, so those branches had stopped firing after the rename).
- README and LEARN research sections rewritten for the 30-scenario, t+1 re-baseline and the
  measured hurdle; 0.3.x live-model numbers labelled as belonging to the old 21-scenario set.

### R2 – signal-research quant correctness (0.4.0)

Implements items 2.1–2.14 of `docs/review/2026-11-07/PLAN.md`, in the binding order
2.1 → 2.9 → 2.10 → hurdle → 2.11.

- **2.1 Execution at t+1.** `execution_lag_days` (default 1) and `fill` (`close` | `open`) in the
  backtest, scope and MCP tool. Legacy R03 reversal: net Sharpe 1.73 at lag 0, −0.61 at lag 1
  (review: 1.80 → −0.67).
- **2.2 Weight drift** between fills; turnover from drifted to target weights.
- **2.3 Returns and financing.** Sharpe/Sortino on excess returns; cash = 1 − net earns rf (short
  rebate), negative cash pays rf + 50 bps; borrow 0.5/2/10 % by tier; 2x Reg T cap; Reg SHO SSR
  blocks logged; cost breakdown (spread, impact, borrow, financing). The Ken French rf CSV is
  **not committed** (a file download needs the owner's explicit OK); `scripts/fetch_rf.py` builds
  it; until then rf = 0, stated in every backtest's `assumptions`.
- **2.4 NYSE calendar** 2000–2030 (`ceap.data.calendar`, committed `data/reference/nyse_calendar.csv`
  checked against the rules), early closes, special closures, aware datetimes only.
- **2.5 Data policy.** ADV lagged one day; merger/performance delisting bounds both reported;
  explicit NaN policy; per-name weight and ADV caps inside the loop, logged.
- **2.6 Capacity sweep** 10–500 M with breakeven AUM and half-Sharpe AUM.
- **2.7 Statistics.** Gate on non-overlapping holding-period IC after the lag; Newey-West t;
  `ic_ir_annualised`; `turnover` renamed `rank_autocorrelation_turnover`; in-sample tail trimmed.
  Null |t| > 2 rate 6.0 % (500 seeds × 6 families).
- **2.8 Trial ledger and multiple testing.** Enumerated 24-configuration search space; append-only
  server-side ledger; deflated Sharpe from ledger trials; `OVERFIT` → `OOS_DECAY`, new
  `REGIME_BREAK`; locked `in_sample_end`; `HypothesisTable` on every assessment.
- **2.9 Generator** `realistic_v1`: 20 years, GARCH + fat tails, latent drift/transient premia,
  crash, volatility-dependent liquidity, listings/delistings; parameter-derived ground truth
  (`ceap.data.ground_truth`); power curve `docs/quant_power_curve.json`.
- **2.10 Null sweep and hurdle.** `ceap evaluate --suite null`. 500 seeds × 24 configurations:
  hurdle **t = 0.65** (15/500 false PROMOTEs, 3.0 %, band [1.7 %, 4.9 %]), sweep hash
  `dfdadabefeb7682e71e86d3ca8dfc97e2811a48c3bf118bf75e94f07373fe5c0`, in
  `data/reference/research_thresholds.json`.
- **2.12 Risk.** Beta vs market ex-self with Vasicek shrinkage; factor, short-squeeze and
  liquidation stress; limits over the out-of-sample history with the worst date.
- **2.13 Membership mask** honoured by signals, IC, backtest and risk.
- **2.14 Profitability table** (`ceap profitability`), attached to every backtest result.

#### Breaking / migration

- `ResearchFlag.OVERFIT` is an alias of `OOS_DECAY` (value `"OOS_DECAY"`); stored `"OVERFIT"` is
  mapped on read. `PeriodSignalStats.turnover` is now a read-only alias of
  `rank_autocorrelation_turnover`.
- Backtests default to a t+1 close fill, drift, financing and caps; `run_backtest` refuses
  rebalance/weighting choices outside the search space via the MCP tool.
- Datasets are 5040 days with NaN prices outside membership; research catalogue is 30 scenarios.

#### 2.11 re-baseline: ground truth before and after (decision D5: flips stay REJECT)

| Scenario | Template | 0.4 truth | 0.5 truth (from parameters, t+1) | Change |
|---|---|---|---|---|
| RS01–03 | momentum_premium | PROMOTE | PROMOTE | same |
| RS04–06 | no_alpha | REJECT · NO_ALPHA | REJECT · NO_ALPHA | same |
| RS07 | reversal_premium | PROMOTE | REJECT · COST_DRAG | **flip** |
| RS08 | reversal_premium | PROMOTE | REJECT · COST_DRAG | **flip** |
| RS09 | reversal_premium | PROMOTE | REJECT · COST_DRAG | **flip** |
| RS10–12 | regime_break | REJECT · OVERFIT | REJECT · OOS_DECAY | flag renamed; premium now vanishes after the split |
| RS13–15 | cost_drag | REJECT · COST_DRAG | REJECT · COST_DRAG | same |
| RS16–18 | concentration | REJECT · CONCENTRATION, LIMIT_BREACH | same | same |
| RS19 | mixed | PROMOTE | REJECT · COST_DRAG (near) | **flip** |
| RS20 | mixed | PROMOTE | REJECT · COST_DRAG (near) | **flip** |
| RS21 | mixed | PROMOTE | REJECT · COST_DRAG (near) | **flip** |
| RS22–24 | momentum_near_threshold | – | PROMOTE (near) | new |
| RS25–27 | reversal_weak | – | REJECT · COST_DRAG | new |
| RS28–30 | momentum_crash | – | PROMOTE | new |

Results (mock model): 29/30 verdicts, clear 23/24, near-threshold 6/6, flags 0.90.

## 0.3.5 – Two new dedicated pages: AI/agents and quant

0.3.4 folded Stage 2 and the real-model findings into the *existing* docs but added no new
standalone page, so there was nowhere a reader could go to see "how does LLM/AI/agents work
here" or "how does quant work here" as its own answer. Added:

- `docs/ai-agents/ai-agents.md` – the LLM/AI/ML/agent boundary, where the model is actually
  called (and where it explicitly is not), the full agent roster across both pipelines, how the
  harness owns governance rather than the plan, the critic and narrative audits, the honest
  real-model findings from 0.3.1, and an explicit "what is not here" section (no trained ML
  models despite the `ml` extra, no autonomous trading loop, no fine-tuning/memory).
- `docs/quant/quant.md` – the deterministic quant layer for both verticals (execution-quality TCA
  and Stage 2 signal research), the formulas and thresholds behind `attribute_causes` and
  `assess_research`, why the synthetic generators are built to share code with the analytics
  that grade them, and an explicit "what is not here" section (no optimiser, no commercial risk
  model, no live data/OMS, no ML).

Linked from `README.md`, `docs/INDEX.md`, `docs/GITHUB_PAGES.md` and the `docs/index.html`
navigation bar.

## 0.3.4 – Documentation pass: catch up every doc with Stage 2 and the real-model findings

No code changes. `README.md`, `LEARN.md`, `COOKBOOK.md`, `docs/architecture/overview.md`,
`docs/threat-model/threat-model.md`, `docs/evaluation/evaluation.md`, `docs/DIAGRAMS.md`,
`docs/GITHUB_PAGES.md` and `docs/index.html` had drifted since v0.3.0-0.3.3 shipped Stage 2 and
the real-model bug fixes. Notably:

- The threat model's scope statement claimed the platform "does not place, amend or cancel
  orders" - no longer true since Stage 2's `execution.stage_orders`. Corrected, with three new
  threats (T17-T19) covering the order-staging trust boundary and its tests.
- The architecture overview, MCP topology diagram and `LEARN.md`'s tool table were still
  describing 5 servers / 26 tools; the platform now has 8 servers / 37 tools, and the agent
  roster tables only listed the 6 investigation agents, missing the 4 research agents.
- `LEARN.md` and `COOKBOOK.md` had zero mentions of Stage 2; added a guided-tour section and six
  cookbook recipes (research CLI/Python/API, order staging, adding a signal, adding a research
  scenario) at the same depth as the existing execution-quality content.
- `docs/index.html`'s measured-numbers block was still quoting 138 tests (now 212) and had no
  research-suite numbers; added them, plus a CI badge on `README.md` now that CI actually passes.
- Evaluation and cookbook docs now carry the honest real-model findings from 0.3.1 (planner
  model matters, two bugs the mock had hidden) instead of only the mock LLM's numbers.

## 0.3.3 – Fix CI for real: `pip-tools` has no cross-platform lock mode

0.3.2's fix (dropping the self-referential lockfile line) was necessary but not sufficient - the
next CI run failed on `pywin32==312`, a Windows-only transitive dependency of `mcp` with no
distribution for Linux. Root cause: `pip-tools` resolves `requirements.lock` for whatever
interpreter and OS it runs on (there is no `--universal`/cross-platform mode as in some other
resolvers), so regenerating it on Windows always reintroduces both the self-referential entry and
`pywin32` with no platform marker. Removed `pywin32` and added a comment at the top of
`requirements.lock` documenting both entries to strip by hand after every future regeneration.
CI verified green end-to-end after this fix (not just locally - see the process note below).

## 0.3.2 – Fix CI: drop the self-referential lockfile entry (incomplete)

CI has been failing since `requirements.lock` was introduced in 0.2.1 (never verified after
release - see the process note below). `pip-compile` recorded the project itself as a
`file:///C:/Work/Claude/...` requirement - a Windows absolute path that cannot resolve on the
Linux runner (`pip install -r requirements.lock` failed with `No such file or directory:
'/C:/Work/...'`). CI's second install step (`pip install -e . --no-deps`) already installs the
project itself, so the self-referential line in the lockfile was both wrong and redundant;
removed it. This alone did not fix CI - see 0.3.3.

Also (unreleased, from local testing): tried deriving pairwise relative-percent-change values for
the narrative number audit to reduce false positives on legitimate arithmetic; reverted after it
let a genuinely fabricated number ("99.9%") pass the audit undetected in the adversarial suite -
the fix made the audit's core fabrication-detection guarantee worse than the noise it removed.

**Process note:** none of v0.2.1, v0.3.0 or v0.3.1's CI runs were checked after release - all
three failed, unnoticed, until 0.3.2/0.3.3. Going forward, a release is not done until the actual
GitHub Actions run for that push is confirmed green, not just the local test suite.

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
