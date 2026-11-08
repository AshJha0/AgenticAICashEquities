# CEAP remediation plan v2 (consolidated, for execution by new sessions)

Date: 2026-11-07. Status: SAVED, NOT STARTED. Supersedes `PLAN_v1_superseded.md` and
`AI_PLAN_v1_superseded.md`, which stay for the record. The critique that produced this version is in
`PLAN_REVIEW.md`. The code review it answers is `REVIEW.md`.

**Owner delegation (2026-11-07):** "all the decisions = you decide for me (no money to be spent, no
card details to be given)". Every decision is recorded in section 9. No session asks the owner for
one that is already there. Hard constraints: zero API spend, zero paid services, zero sign-ups that
ask for a card, no live model calls, free daily data only (Stooq, yfinance, Ken French library),
public educational repository, nothing trades.

## 0. How to read this document

- **Effort** is engineering hours. Rounds roll up to plan-days (8 h). Wall-clock assumes one owner
  reviewing at most two substantial diffs a day plus two Claude sessions; the critique showed three
  parallel sessions outrun the reviewer.
- **Cost** is always $0. Where an item has a live-model variant it is listed as NOT RUN LIVE and the
  acceptance test is offline.
- **Decision** names the entry in section 9 that governs the item; `none` means maintainer judgement.
- **Done when** is the acceptance test. Where it names a count or rate it also names the sample size
  and the tolerance; a criterion without those is not done.
- **Dependencies** are explicit. The v1 claim that rounds 2, 3 and 4 were disjoint was false; section 7
  shows the file-conflict map that drives the new order.

## 1. Goal, non-goals, release train

Goal: the project tells the truth at every layer and can prove it offline. Governance cannot be
bypassed, no degradation is silent, every model-dependent behaviour is either guaranteed by the
harness for any model output or labelled UNKNOWN, the quant is correct under realistic execution
timing with hurdles derived from measured null distributions, state survives a restart, and one
preregistered free-data study has been run with a power analysis and reported whatever it says.

Non-goals: live trading, OMS/EMS connectors, UI, SSO, paid data, any ML model trained on real data,
any claim about live model quality. Models fitted on synthetic data are labelled "fitted to synthetic
generator <hash>".

Release train (decision D16). One minor version per round so each is small enough to roll back.

Revised 2026-11-07 by owner instruction (maximum parallelism, one combined PR per release): R2 moved into 0.4.0, so later releases shift down one and RP paper trading joins 0.7.0.

| Version | Rounds | Must ship by |
|---|---|---|
| 0.4.0 | R0 foundations + R1 fail-closed + R2 quant correctness (combined, owner 2026-11-07) | shipped 2026-11-07 (PR #1) |
| 0.5.0 | R2T TCA realism + R4 platform and persistence + Ken French rf series | shipped 2026-11-08 (PRs #2, #3) |
| 0.6.0 | R3 AI (3A, 3B, 3C, 3E) + R2B ML | |
| 0.7.0 | R5 real-data study (VOID under its preregistration: coverage 75.1 % < 95 %) + RP paper trading | |
| 1.0.0-rc1 | only when API and database schema are frozen; not planned here | |

## 2. Rounds, items, estimates

### R0: foundations that every other round depends on. Branch `r0-foundations`. 0.4.0 part 1

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 0.1 | Dev bootstrap: `scripts/bootstrap.ps1` and `.sh` build `.venv` from the lock; documented in README | `scripts/`, `README.md` | 2 h | none | Fresh Windows clone: `pytest --co -q` reports 0 errors (today: 5, no fastapi/httpx) |
| 0.2 | Universal hashed lock with `uv pip compile --universal --generate-hashes` (free); CI job `lock-fresh` regenerates on Linux and fails if the committed lock differs; remove the hand-edit header, remove unused `xgboost` from the `ml` extra | `requirements.lock`, `pyproject.toml`, `.github/workflows/ci.yml` | 5 h | none | Lock has hashes; installs on ubuntu 3.11 and 3.12; no hand edits; `lock-fresh` green |
| 0.3 | `.gitattributes` with `* text=auto eol=lf`; all hashes (prompts, audit chain) computed on canonical bytes (LF, sorted-key JSON, fixed datetime format, no NaN) | `.gitattributes`, `ceap/util/canonical.py` | 2 h | none | Test: CRLF and LF copies hash identically; repo renormalised in one commit |
| 0.4 | CI hardening first, not in Round 4: `permissions: contents: read`, concurrency group, actions pinned to SHAs, pip cache, matrix 3.11 and 3.12, coverage floor 80 % rising to 85 % by 0.7, `pip-audit`, gitleaks, Docker build and `ceap --help` smoke, `release.yml` triggered by tag that verifies check-runs green on the SHA, version agreement in three places, CHANGELOG section present, the banned-word check (`scripts/check_banned_words.py`) empty, licence allowlist, then builds wheel and image and creates the release | `.github/workflows/ci.yml`, `release.yml`, `scripts/release_check.py` | 10 h | D8 | A tag on a red SHA produces no release; PR CI p50 under 8 min over 5 runs |
| 0.5 | Branch protection on `main`: required checks, 0 required approvals (solo maintainer), linear history off, force-push off. Owner toggles it (free, 5 min) | GitHub settings | 0.5 h | D8 | Direct push to main refused; PR with red checks cannot merge |
| 0.6 | Test tiers: `slow` and `nightly` markers; function-scoped `platform` fixture (today session-scoped, state leaks); `pytest-xdist`; `hypothesis` with `derandomize=True` and 100 examples on PR, 1000 nightly; `nightly.yml` | `tests/conftest.py`, `pyproject.toml`, `.github/workflows/nightly.yml` | 8 h | none | PR suite p50 under 8 min; nightly green; no test depends on another's state (random order run green) |
| 0.7 | Contracts PR: freeze the shapes every later round writes to before parallel work starts: `TaskStatus` adds `DEGRADED`, `INCOMPLETE_ANALYSIS`, `INTERRUPTED`; `Result.warnings: list[Warning]`; `Finding.evidence_refs: list[EvidenceRef{id, path, value}]`; `HypothesisTable`; `LLMCallRecord` (model, sdk, prompt_sha, tokens, cache tokens, usd, latency, request_id); `ToolResult.schema_version`; `ApprovalRequest.content_hash`; `schema_version` on every persisted JSON | `domain/tasks.py`, `domain/findings.py`, `domain/reports.py`, `domain/tools.py`, `domain/policy.py` | 8 h | none | CI diff check: later rounds change no `domain/*` public signature without a `contracts-change` label |
| 0.8 | Secret and PII hygiene: gitleaks in CI; question text redacted (emails, keys, tokens, account-like numbers) before any log, store or prompt; cassette scrubber strips `x-api-key`, request ids, org ids; retention settings documented | `ci.yml`, `ceap/util/redact.py`, `observability/logging.py` | 6 h | none | Planted fake key in a test fixture fails CI; scrubber round-trip test passes |
| 0.9 | CHANGELOG discipline: `## Unreleased` section with "Breaking / migration" subsection; CI check that every PR touches it unless labelled `no-changelog` | `CHANGELOG.md`, `ci.yml` | 1 h | none | PR without CHANGELOG change fails the check |

R0: 42.5 h, about 5.5 plan-days, 2 wall-clock days. One session.

### R1: fail closed by default. Branch `r1-fail-closed`. 0.4.0 part 2

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 1.1 | `build_router` raises `SettingsError` when provider is `anthropic` and the client cannot be built; mock only when provider is explicitly `mock`; `CEAP_LLM_PROVIDER=auto` removed (breaking, in CHANGELOG) | `llm/router.py`, `config.py` | 2 h | none | Startup with provider anthropic and no SDK fails; API never reports `ok` on an unrequested mock |
| 1.2 | Dockerfile: Python 3.12, non-root, install `.[llm]` from the hashed lock, HEALTHCHECK, `.dockerignore`, digest-pinned base; built in CI (0.4) | `Dockerfile`, `.dockerignore` | 2 h | none | CI builds the image and `python -c "import anthropic"` succeeds inside it |
| 1.3 | Four-eyes: new `approver` role holds `approvals:decide`; `admin` loses it; `decide` rejects `decided_by == requested_by` with 403; staging approval must come from a principal different from the proposal approver (T17 clarified); system actors (`timeout`) exempt from the check and audited | `policy/permissions.py`, `policy/approvals.py`, `api/routes.py`, `docs/threat-model` | 5 h | D1 | Adversarial tests: same principal cannot request and approve; proposal approver cannot approve staging; two distinct principals can |
| 1.4 | Harness default gateway is deny; auto-approve only when `env=dev` and `CEAP_AUTO_APPROVE=true`; non-dev with auto-approve refuses to serve | `harness/engine.py`, `api/app.py`, `config.py` | 2 h | none | `AgentHarness()` without a gateway denies; serving test fails fast |
| 1.5 | Degradation ladder, explicit and visible: rung 0 model ok; rung 1 server-side refusal fallback to another model (recorded); rung 2 structured-output repair retry (once); rung 3 canonical plan or templated narrative (recorded); rung 4 `CEAP_LLM_DISABLED=true` kill switch forcing rung 3 everywhere with a banner. Every rung above 0 appends a `Warning` and sets `DEGRADED`; `stop_reason` of `max_tokens` or `refusal` recorded with `stop_details` | `llm/router.py`, `agents/planner.py`, `agents/critic.py`, `agents/reporter.py`, `harness/engine.py`, `config.py` | 8 h | none | One test per rung asserts the warning text and `DEGRADED`; kill switch test shows banner |
| 1.6 | Required specialist agents: harness appends any of market, execution, quant, risk, engineering a plan omitted; a failed agent yields `INCOMPLETE_ANALYSIS`; `primary` has no default anywhere; a timed-out agent returns `PARTIAL` output rather than nothing | `harness/engine.py`, `agents/reporter.py`, `llm/client.py`, `agents/base.py` | 6 h | none | Plan without `quant` still runs quant; forced agent failure never yields "no material anomaly"; partial output test |
| 1.7 | MCP trust fail-closed in all three places: `adapter.py:78-79`, `client.py:133`, `client.py:168`. Missing `readOnlyHint` means not read-only, missing `riskLevel` means HIGH; `trusted_servers` allowlist; untrusted servers are read-only regardless of hints; tool results carry `schema_version` and a drifted version is rejected | `mcp/adapter.py`, `mcp/client.py`, `config.py`, `policy/engine.py` | 5 h | none | Unannotated tool from an untrusted server denied for any write, through both in-process and stdio paths |
| 1.8 | Standalone FastMCP servers enforce `required_capabilities` from an env-provided principal | `mcp/server.py`, `mcp/*/__main__.py` | 3 h | none | stdio: `stage_orders` without `trading:execute` returns a policy error |
| 1.9 | Model and SDK pins: `llm/model_pins.toml` with allowed ids (`claude-opus-5-5` planning, `claude-sonnet-5-5` narrative and critique, `claude-haiku-5-5` judge paths) and SDK version; unpinned id refused; `LLMRequest.temperature` removed; `max_tokens` derived from effort with thinking headroom (planner 16000, critic 8000, reporter 8000 at the chosen effort; today 4096/2048/3000 would truncate once thinking is on); per-call timeout `CEAP_LLM_TIMEOUT_S` default 60; LLM calls under `race_cancellation`; model-call concurrency cap and per-task call cap | `llm/model_pins.toml`, `llm/client.py`, `llm/models.py`, `harness/engine.py`, `config.py` | 6 h | D14 | `git grep sonnet-4-5` empty; unpinned id test fails; cancel mid-call returns in under 1 s; 400 not retried, 429 and 5xx retried |
| 1.10 | Public hygiene: remove the banned job-prep word (README, LEARN section 17 retitled "Knowledge check", docs/INDEX); `git rm "Claude outputs/ci.yml"`; README banner: synthetic data, nothing trades, paper orders only, no live model numbers; fix `.venv\Scripts` escaping | `README.md`, `LEARN.md`, `docs/INDEX.md` | 1 h | none | the banned-word check (`scripts/check_banned_words.py`) empty; banner in the first screen |
| 1.11 | Tests named per item in the PR description; threat model T17 corrected, T20 untrusted MCP, T21 construction-time fallback, T22 kill switch | `tests/**`, `docs/threat-model` | 5 h | none | PR lists one test id per item 1.1-1.10; `pytest --co` shows at least 20 new tests; CI run id recorded in CHANGELOG |

R1: 45 h, about 5.5 plan-days, 2 wall-clock days. Tag 0.4.0 after R0 and R1 merge.

### R2: signal-research quant correctness. Branch `r2-quant`. 0.4.0 part 3

Order inside the round matters: 2.1 → 2.9 (longer history) → 2.10 (null sweep) → choose hurdle → 2.11.

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 2.1 | Execution at t+1: `execution_lag_days` in `BacktestSpec`, default 1, fill at next close or next open; lag-sensitivity test | `analytics/backtest.py`, `domain/research.py`, `mcp/backtest/server.py` | 4 h | none | Reversal R03 under lag 1 reproduces the sign flip (1.80 to about -0.67) |
| 2.2 | Weight drift between rebalances; turnover from drifted to target | `analytics/backtest.py` | 3 h | none | Two-name three-day known-answer test |
| 2.3 | Returns and financing conventions: Sharpe on excess returns (Ken French risk-free series, committed as a small CSV with attribution); short rebate and financing on dollar-neutral books; cash drag on long-only; gross leverage limit (Reg T 2x default); borrow by liquidity tier (A 0.5 %, B 2 %, C 10 % per year, stated as assumptions); Reg SHO SSR flag: no new short at the close on a name down 10 % intraday (logged) | `analytics/backtest.py`, `analytics/portfolio_risk.py`, `data/reference/rf.csv` | 8 h | none | Long-only Sharpe falls by rf/sigma in a known-answer test; SSR days logged; borrow line in the cost breakdown |
| 2.4 | Calendar: NYSE holidays and early closes (13:00) 2000 to 2030 as a committed list; special closures (2012-10-29/30, 2018-12-05, 2025-01-09); `zoneinfo` everywhere, no naive datetimes | `data/calendar.py`, `data/historical.py` | 3 h | none | Thanksgiving, Good Friday absent; Black Friday flagged half day; naive datetime raises |
| 2.5 | Data policy: `adv_20` lagged one day; delisting handling with two bounds (merger: last price; performance: -30 % NYSE, -55 % Nasdaq per Shumway 1997) and both reported; NaN policy explicit; per-name position and participation caps inside the loop, breaches clipped and recorded | `data/historical.py`, `analytics/backtest.py` | 5 h | none | Tests per policy; a cap breach appears in the backtest log, never silently traded |
| 2.6 | Capacity: participation-capped sweep over notional {10, 25, 50, 100, 250, 500}M; report breakeven AUM and the AUM at which net Sharpe halves | `analytics/backtest.py`, `agents/backtest.py` | 4 h | none | On a fixed seed, Spearman(notional, net Sharpe) = -1 and the 500M Sharpe is below the 10M Sharpe by more than 2 standard errors; breakeven AUM printed |
| 2.7 | Statistics: gate on non-overlapping horizon-h IC matching the holding period; Newey-West t for overlapping series; `ic_ir_annualised`; rename signal turnover to `rank_autocorrelation_turnover`; drop the last h in-sample dates from in-sample stats | `analytics/signal_statistics.py`, `domain/research.py`, `agents/alpha.py` | 6 h | none | Null test, 500 seeds (nightly): |t| > 2 rate within [3.0 %, 7.5 %]; PR CI 50 seeds within [0, 14 %] |
| 2.8 | Trial ledger and multiple testing: append-only ledger written by the harness (params hash, dataset hash, Sharpe, date); `n_trials` read from the ledger, never from the caller; a re-run of identical params does not increment; deflated Sharpe computed from the ledger's trial variance, skew and kurtosis; `OVERFIT` renamed `OOS_DECAY`; new `REGIME_BREAK`; `in_sample_end` locked per dataset; planner baseline and parameter choices come from an enumerated set and each distinct choice is a trial | `analytics/research_assessment.py`, `domain/research.py`, `harness/engine.py`, `data/research_scenarios.py` | 8 h | D4 | Caller cannot lower `n_trials`; two distinct specs raise the hurdle visibly in the proposal; identical re-run does not |
| 2.9 | Synthetic history to 20 years (5040 days) with realistic premia (target Sharpe 0.3 to 0.8), decay, one crash scenario, volatility clustering, fat tails, spread and volume dependent on volatility; near-threshold scenarios; pure-noise set; ground truth derived from generator parameters (premium vs detectable threshold), never from a run | `data/historical.py`, `data/research_scenarios.py` | 12 h | none | Power curve (detection rate vs premium) committed; at least two scenarios within one standard error of a gate; no `expected_verdict` is produced by running the code |
| 2.10 | Null sweep and hurdle selection: 500 seeds of the no-alpha set (nightly), 50 on PR; false-PROMOTE rate with 95 % binomial band; the PROMOTE hurdle is then chosen as the lowest t such that the upper bound of the false-PROMOTE rate is at most 5 % over the full search the agent can run, and written into `ResearchThresholds` with the sweep's hash | `evaluation.py`, `cli.py`, `analytics/research_assessment.py` | 5 h | D4 | `ceap evaluate --suite null` prints rate and band; thresholds file records the sweep hash |
| 2.11 | Re-derive the 21 ground truths from parameters under t+1 execution; scenarios that flip to REJECT stay REJECT; a dedicated re-baseline PR with a diff table | `data/research_scenarios.py`, `docs/evaluation` | 4 h | D5 | Diff table in the PR; CHANGELOG lists each flip |
| 2.12 | Risk: beta against the market excluding the name with shrinkage toward 1; stress adds factor shock (generator factor), short-squeeze tail (+20 % on shorts), liquidation cost at 10 % ADV; limits evaluated over the whole OOS history | `analytics/portfolio_risk.py`, `mcp/risk/server.py`, `agents/portfolio_risk.py` | 6 h | none | Known-answer tests; worst-date breach reported, not only as-of |
| 2.13 | Membership mask in `HistoricalDataset`, honoured by backtest, IC, z-score and risk (prerequisite for R5) | `data/historical.py`, `analytics/*` | 8 h | none | Property test: a non-member is never ranked or held |
| 2.14 | Profitability table per signal: breakeven cost per unit turnover, cost share of gross P&L at each AUM, drawdown depth and duration, per-year returns, worst months, alpha after the generator's factor model | `analytics/backtest.py`, `agents/backtest.py`, `docs/quant` | 5 h | none | Table reproduced by one command; appears in the proposal |

R2: 81 h, about 10 plan-days, 4 wall-clock days. Second session re-derives every formula from the
diff before merge (3 h).

### R2T: TCA realism. Branch `r2t-tca`. 0.5.0 part 1

Runs after R2 merges (shares `execution_metrics.py` and `attribution.py` with nothing in R2, but the
evaluation and golden numbers must settle once, not twice).

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 2T.1 | Multi-day, multi-order TCA generator: 20 sessions, about 40 parents per session, BUY and SELL, 3 brokers, algos VWAP/POV/IS/MOC, passive and aggressive fills, odd lots, auction prints flagged, late prints, duplicate timestamps, a LULD pause scenario; a synthetic market index for beta adjustment; US session 09:30 to 16:00 America/New_York; U-shaped volume | `data/synthetic.py`, `data/scenarios.py` | 18 h | none | Planted 3 bps broker gap detected at a stated power over 100 seeds; timezone test; auction prints excluded from interval VWAP by default; both sides tested |
| 2T.2 | Re-baseline the 50 execution scenarios on the new generator in a dedicated PR with a diff table; time-of-day-matched baseline (same window on previous k sessions) so U-shaped volume creates no false cause | `data/scenarios.py`, `analytics/attribution.py`, `agents/quant.py`, `docs/evaluation` | 10 h | none | T01 flags deterioration in at most 5 % of 200 seeds; adding U-shape alone produces no cause |
| 2T.3 | Perold IS with delay cost, fees and rebates; components sum test | `analytics/implementation_shortfall.py`, `analytics/execution_metrics.py` | 4 h | none | Components sum to total IS within 1e-9 |
| 2T.4 | IS noise model: analytic standard error sigma*sqrt(T/3) for a linear schedule plus dispersion across peer orders; deterioration is residual z against the pre-trade estimate, not a 1.5 bps delta; attribution scores sit next to a bps decomposition with effect sizes | `analytics/attribution.py`, `analytics/execution_metrics.py` | 6 h | none | T01 false-flag rate at most 5 % of 200 seeds; T05 to T10 still find their cause at or above 90 % |
| 2T.5 | Markouts at 1 s, 10 s, 60 s, 5 min, beta-adjusted against the synthetic index; size-weighted effective and realised spread; price improvement; venue stats conditioned on order type and size bucket; fix `square_root_impact_estimate_bps` units to sigma_daily*sqrt(Q/ADV) and use it as the pre-trade estimate; `volume_share` renamed | `analytics/slippage.py`, `analytics/market_impact.py`, `analytics/execution_metrics.py`, `mcp/execution/server.py` | 12 h | none | Known-answer tests; T01 "impact" reads near zero instead of -45 bps |
| 2T.6 | Peer-relative residual attribution (replaces the v1 "causal DiD"): each parent's IS residual against matched peers (same symbol, size bucket, side, other sessions) with bootstrap CI over peers; no causal claim in the output | `analytics/attribution.py`, `analytics/peers.py` | 8 h | none | Effect size and CI per cause; threshold catalogue kept as fallback |
| 2T.7 | TCA as desks use it: broker/algo/venue comparison normalised by pre-trade estimate; parent/child linkage across sessions; outlier review queue (residual beyond 2.5 SE, reason codes, second-principal sign-off through the approval gateway); a monthly best-execution style report labelled demonstration | `analytics/tca_aggregate.py`, `policy/approvals.py`, `agents/execution.py`, `cli.py` | 12 h | none | Report by one command; an outlier cannot be closed by the principal who raised it |
| 2T.8 | Pre-trade cost model, parametric: fitted square-root model (sigma, participation, spread) with conformal intervals, trained on arrival-time features only, labelled "fitted to synthetic generator <hash>"; coverage tested under shift (fit on 5 scenario families, test on the other 5) | `ml/pretrade_cost.py`, `scripts/train_pretrade.py`, `analytics/execution_metrics.py` | 6 h | D11 | Coverage on shifted families within [85 %, 95 %] at nominal 90 %; model card committed |

R2T: 76 h, about 9.5 plan-days, 4 wall-clock days. Shipped in 0.5.0 with R4.

### R4: platform and persistence. Branch `r4-platform`. 0.5.0 part 2

Can run in parallel with R2 (disjoint files except `policy/approvals.py`, owned by R4 after R1 lands).

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 4.1 | Persistence: SQLite via `sqlite3` behind a single writer thread with `to_thread`, WAL, `busy_timeout`; tables for tasks, results, approvals, staged orders, LLM call records, trial ledger, audit log (hash chain on canonical bytes); `PRAGMA user_version` migrations, forward-only; `ceap db backup` (online backup API) before migrate; `ceap db verify-chain`; retention settings for results, traces and raw model I/O; database size bound | `data/db.py`, `data/repositories.py`, `migrations/*.sql`, `cli.py`, `platform.py`, `policy/approvals.py`, `mcp/execution/staging.py` | 24 h | D7 | Upgrade v1 to v2 passes; backup restores and verifies; Windows file-lock test on `-wal` passes |
| 4.2 | Restart and approval semantics: SIGKILL with a pending approval then restart yields task `INTERRUPTED`, approval `EXPIRED`; late decide returns 409 and is audited; two concurrent decides: conditional `UPDATE ... WHERE status='PENDING'`, exactly one wins | `policy/approvals.py`, `platform.py`, `harness/engine.py` | 8 h | none | Three tests as named |
| 4.3 | Ownership scoping on all reads; new `auditor` role reads all | `api/routes.py`, `policy/permissions.py` | 3 h | none | Viewer cannot read another principal's task; auditor can |
| 4.4 | Semaphore released while awaiting approval; `CEAP_APPROVAL_TIMEOUT_S`; idempotency key header scoped per principal, same key with a different body returns 422, keys persisted, TTL 24 h; request body limit and question length limit; `gross_notional` upper bound; `paginate` clamps | `api/routes.py`, `api/models.py`, `policy/approvals.py`, `mcp/common.py`, `config.py` | 6 h | none | Four pending approvals do not block a fifth task; duplicate POST returns the same id; different body 422 |
| 4.5 | Approval payload `content_hash` bound to the recomputed portfolio; `stage_orders` refuses on mismatch (TOCTOU closed) | `mcp/execution/server.py`, `mcp/execution/staging.py`, `harness/engine.py` | 4 h | none | Dataset mutated between approval and staging: staging refuses |
| 4.6 | Auth: salted key hashes, optional expiry, correlation id per request, `/health` liveness only, `/metrics` behind a key, raw key never printed | `api/auth.py`, `config.py`, `api/routes.py`, `observability/logging.py` | 5 h | none | Raw key absent from logs and errors; every log line has a request id |
| 4.7 | Task replay bundle: inputs, tool outputs, model I/O (redacted), prompt hashes, seeds; `ceap replay <bundle>` reproduces the result byte for byte with injected clock | `harness/replay.py`, `cli.py` | 8 h | none | Replay of a stored bundle equals the stored result |
| 4.8 | Observability: Prometheus exposition fixed (`promtool check metrics` passes); HTTP latency and error metrics; one OpenTelemetry test | `observability/*.py`, `api/app.py` | 3 h | none | `promtool` passes; OTel span test green |
| 4.9 | Property tests for analytics invariants with `hypothesis` (IS components sum, weights sum, cost >= 0, impact monotone in participation, VWAP within [low, high]); MCP schema contract tests; golden report snapshots (after R2T re-baseline) | `tests/properties/`, `tests/contract/`, `tests/golden/` | 8 h | none | Named invariants, 100 examples on PR, 1000 nightly, derandomised |

R4: 69 h, about 8.5 plan-days, 3.5 wall-clock days. Shipped in 0.5.0 (2026-11-08).

### R3A: LLM layer, offline-provable. Branch `r3a-llm`. 0.6.0 part 1

Runs after 0.4.0. Everything here must be shown done with no live call.

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 3A.1 | Structured outputs: planner and critic via `client.messages.parse()` with strict Pydantic schemas; `Plan` steps become a discriminated union over the tool catalogue generated from MCP input schemas (`additionalProperties: false` everywhere); semantic validation after schema validation (unknown agent, wrong window, symbol outside universe) is the repair target, once; the reporter is prose and does not use structured output; `extract_json` deleted | `llm/client.py`, `llm/schemas.py`, `agents/planner.py`, `agents/critic.py` | 12 h | none | Schema-subset lint passes for every schema; semantic-invalid fixture triggers exactly one repair then `DEGRADED`; no regex JSON path remains |
| 3A.2 | Wire contract tests with the real `AsyncAnthropic` over `httpx.MockTransport`: request snapshot asserts no `temperature`/`top_p`, no forced `tool_choice`, `strict: true`, `output_config.format` present, `cache_control` on the last stable block in tools→system→messages order, `max_tokens` headroom for thinking at the chosen effort, model id in the pin file; responses validated with `anthropic.types.Message.model_validate` including `thinking`, `redacted_thinking`, `tool_use`, citations of both location types, `refusal` with `stop_details`, `usage.cache_*` | `tests/contract/test_wire_request.py`, `test_wire_response.py`, `tests/fakes/transport.py` | 8 h | none | SDK schema drift fails CI; request snapshot committed and reviewed |
| 3A.3 | Adversarial fake clients: `SchemaFuzzClient` (hypothesis-jsonschema from `Plan.model_json_schema()` plus mutations), `TruncatingClient`, `RefusalClient`, `HangingClient`, `RaisingClient` (every SDK error class), `ObedientInjectionClient` (obeys any instruction found in its input), `NumberFabricatorClient`, `IdFabricatorClient`, `HostilePlanClient` (symbols outside universe, duplicate governance steps, 500 steps) | `tests/fakes/clients.py` | 6 h | none | Nine clients, each used by at least one test |
| 3A.4 | Harness invariant property tests, run under every fake client: governance tail present and ordered; no tool outside the catalogue runs; pinned arguments and windows unchanged; critic only lowers confidence; any model fault gives `DEGRADED` never `ok`; approval never automatic and requester differs from approver; report numbers and ids trace to facts (becomes blocking, not a warning); model calls per task at most N and wall time at most T; same inputs and cassette give byte-identical result with injected clock | `tests/properties/test_harness_invariants.py` | 10 h | none | Ten invariants, 100 examples on PR and 1000 nightly, all green under `ObedientInjectionClient` |
| 3A.5 | Cassettes with provenance: header `{provenance: hand_built or recorded, model, sdk, request_sha256, prompt_sha}`; hand-built cassettes written in the full wire format including `thinking` blocks and `redacted_thinking`, authored from the golden set (3E.1), not from the mock; replay fails `STALE` when the request hash changes; CI refuses to publish any model-behaviour metric from a `hand_built` cassette | `tests/cassettes/schema.json`, `tests/cassettes/*.json`, `llm/recording.py`, `evaluation.py` | 10 h | D15 | Prompt change makes replay fail `STALE`; metric gate test; scrubber (0.8) applied on write |
| 3A.6 | Per-call `LLMCallRecord` persisted (4.1) with redacted body; per-task cost span; `CEAP_TASK_BUDGET_USD` enforced from a local token estimate (not `count_tokens`, which is a network call) with the estimate labelled; prompt caching request shape in place (verified by 3A.2 snapshot, never by live hit rate) | `llm/client.py`, `llm/router.py`, `harness/engine.py`, `observability/metrics.py` | 6 h | none | Two concurrent tasks report separate costs; a $0.01 budget aborts before the call with `DEGRADED` |
| 3A.7 | Prompt registry: prompts as versioned files with sha256 on canonical bytes; version and hash in result and trace; prompt change requires a CHANGELOG entry (CI check) | `llm/prompts/*.md`, `llm/prompts.py`, `domain/reports.py` | 3 h | none | Changing a prompt changes the golden snapshot hash and fails CI without a CHANGELOG line |
| 3A.8 | Adapter equivalence (replaces v1 "mock parity"): the mock path and the adapter-plus-cassette path produce identical harness outputs apart from narrative; labelled "adapter equivalence", never "model fidelity" | `tests/contract/test_adapter_differential.py` | 4 h | none | Structural diff empty on the 10 template scenarios |

R3A: 59 h, about 7.5 plan-days, 3 wall-clock days.

### R3B: agents and agentic AI, within the boundary. Branch `r3b-agents`. 0.6.0 part 2

Runs after R3A (schemas) and after R2T (TCA numbers settled). Serial with R3A; touches every agent.

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 3B.1 | Planner owns scope within enumerated choices: which agents run, which optional tools to fetch, which baseline from an enumerated set (each distinct choice is a trial in the ledger), which pre-registered hypotheses to test; agents consume plan-fetched data; `ensure` self-serving falls from 28 to at most 8 (grep); required agents stay harness-enforced | `agents/planner.py`, `agents/base.py`, all `agents/*.py`, `llm/prompts/planner.md` | 16 h | none | With a fixed cassette, the chosen agents and tools change the harness call log (asserted); `ensure` count at most 8; a baseline outside the set is rejected |
| 3B.2 | Hypothesis table, deterministic verdicts: each hypothesis type maps to a pre-registered deterministic test (`analytics/hypotheses.py`); the model proposes and ranks hypotheses; the code marks supported, refuted, untested; the report shows all three | `domain/findings.py`, `analytics/hypotheses.py`, `agents/planner.py`, `agents/critic.py`, `agents/reporter.py` | 10 h | none | Report has "untested hypotheses"; T10 lists both causes with separate evidence; no verdict is produced by model text |
| 3B.3 | Critic disputes through different deterministic checks: a dispute counts only when the critic names an alternative check (another baseline, a peer set, a different horizon) whose deterministic result contradicts the finding's value at its `evidence_refs` path; the dispute runs through the policy path with caps on calls; `supported=False` without a named check is recorded but changes nothing; free text stored, not forwarded | `agents/critic.py`, `analytics/hypotheses.py`, `harness/engine.py`, `llm/prompts/critic.md` | 14 h | none | Cassette where a named alternative baseline overturns a finding; `ObedientInjectionClient` cannot lower any confidence without a contradicting deterministic result |
| 3B.4 | Evidence binding: `evidence_refs {id, path, value}` on every finding; validation checks the value at the path; agents cite the attribute they used; `knowledge` becomes `DOC` evidence; `derived_from` verified for `CALCULATION` evidence | `harness/memory.py`, `mcp/adapter.py`, all `agents/*.py` | 14 h | none | Finding whose cited value does not match is dropped with a warning; `IdFabricatorClient` produces zero surviving findings |
| 3B.5 | Injection hygiene per surface: tool-derived text wrapped in delimited data blocks with ids on every surface (question, logs, deployment text, RAG documents, MCP tool descriptions, case memory); per-surface threat model entries and one test per surface under `ObedientInjectionClient` | `llm/prompts.py`, `agents/engineering.py`, `agents/reporter.py`, `docs/threat-model`, `tests/adversarial/test_surfaces.py` | 8 h | none | Six surface tests green; threat model lists each |
| 3B.6 | Typed agent messages and re-planning: agents exchange versioned Pydantic messages instead of an untyped `state` dict; one bounded re-plan attempt on tool or agent failure with the failure reason; context budget per call with a documented truncation order | `domain/agents.py`, `harness/engine.py`, `agents/base.py` | 10 h | none | Drifted message version rejected; forced tool failure triggers exactly one re-plan; oversized facts truncated in the documented order with a warning |
| 3B.7 | Case memory, bounded: after human approval the harness writes a compact case record; the planner retrieves the three nearest past cases as read-only context; memory is OFF in evaluation and partitioned by dataset; append-only, audited; no memory tool, the harness injects | `harness/memory.py`, `rag/retrieval.py`, `agents/planner.py`, `evaluation.py` | 6 h | D10 | Evaluation run shows zero memory reads; second similar investigation cites a prior case id |
| 3B.8 | Human-in-the-loop payload: approver sees the hypothesis table, the evidence links, the diff against the pre-trade estimate and the critic's disputes; reject and modify reasons are fed back into the audit log and the case record | `policy/approvals.py`, `api/models.py`, `api/routes.py` | 5 h | none | Approval payload snapshot test; a rejection reason appears in the audit chain |
| 3B.9 | Stdio MCP lifecycle: `AsyncExitStack` owned by a dedicated task; one reconnect on child exit; test marked `slow` | `mcp/client.py` | 3 h | none | Kill-the-child test passes on Linux CI; marked `slow` |

R3B: 86 h, about 11 plan-days, 5 wall-clock days.

### R3C: RAG, grounded and measured. Branch `r3c-rag`. 0.6.0 part 3

Parallel with R3B (disjoint files except `agents/reporter.py`, owned by R3B; R3C's reporter change is
the two-call design and lands after R3B).

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 3C.1 | Honest naming and BM25: "semantic" removed; BM25 over chunks with metadata (document, section, version, effective date); chunking strategy chosen by the retrieval eval (sentence vs section vs fixed with overlap), not assumed | `rag/*.py`, `mcp/knowledge/server.py` | 6 h | none | Eval table across three chunkings committed; winner is the default |
| 3C.2 | Retrieval eval: 30 queries written before tuning with a 20/10 train/held-out split; expected documents and passages; 10 negative queries that must return nothing above threshold so the report can say "no policy found"; recall at 3 and MRR on the held-out split published; CI floor | `tests/unit/test_rag_eval.py`, `data/reference/knowledge/eval.json` | 5 h | none | Held-out recall at 3 at or above the committed floor; negatives return empty |
| 3C.3 | Query rewriting: the retrieval query is built deterministically from the parsed question (symbol, window, cause candidates) instead of a fixed string; role-filtered retrieval by document classification | `rag/retrieval.py`, `agents/planner.py`, `policy/engine.py` | 4 h | none | Two different questions produce two different queries; viewer cannot retrieve admin-only documents |
| 3C.4 | Two-call reporter with citations: call 1 (structured output) emits `claims[{section, text, fact_keys, evidence_ids, needs_policy_support}]`, validated deterministically; call 2 (citations on, no structured output) writes prose with knowledge chunks as `document` blocks of custom content type, one per chunk, `cache_control` on documents; `content_block_location` maps to chunk ids; harness checks `cited_text` equals the chunk text for the corpus version hash, then mints `DOC` evidence with the span; number audit runs on the result | `agents/reporter.py`, `llm/client.py`, `domain/evidence.py`, `llm/prompts/reporter_*.md` | 12 h | none | Wire contract snapshot for both calls; a fabricated quote in a cassette fails the span audit; corpus hash in the report footer |
| 3C.5 | Corpus versioning and growth: documents carry version and effective date into evidence; the report records the corpus hash; add `docs/quant/quant.md`, threshold tables and TCA methodology as retrievable documents | `data/reference/knowledge/*`, `rag/ingestion.py` | 3 h | none | Query "why this deterioration threshold" retrieves the thresholds section with its version |

R3C: 30 h, about 4 plan-days, 1.5 wall-clock days. Local dense embeddings are deferred (D6 revised):
`sentence-transformers` pulls torch (multi-GB on Linux) into the lock and needs a weight download; not
in 0.x.

### R2B: ML, deterministic and labelled. Branch `r2b-ml`. 0.6.0 part 4

Runs after R2T (needs the multi-order TCA dataset and settled numbers). Each model: trained by a
script, versioned by hash in a registry with a model card, loaded by hash, outputs are `CALCULATION`
evidence carrying the model hash. Label everywhere: "fitted to synthetic generator <hash>".

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 2B.1 | Anomaly and change-point detection: robust z plus CUSUM/PELT (in-house, no new dependency) on per-minute features; false-alarm rate measured as ARL0 over at least 500 clean synthetic sessions; alarm budget tuned to a stated cost per false alarm; interpretable output (which feature, when) | `ml/anomaly.py`, `agents/market.py`, `agents/engineering.py` | 10 h | none | T06 onset within 2 minutes of the planted deployment in at least 95 % of 200 seeds; ARL0 at or above 20 sessions on clean data |
| 2B.2 | Signal combination: ridge over the three z-scored signals with purged and embargoed walk-forward CV; every hyperparameter tried is a ledger trial; deflated Sharpe from the ledger; gradient boosting and meta-labeling dropped | `analytics/ml_signals.py`, `mcp/alpha/server.py`, `agents/alpha.py` | 10 h | D12 | On the realistic set the combination's OOS deflated Sharpe is reported next to each single signal; on the null set the PROMOTE rate over 200 seeds is within the hurdle's band |
| 2B.3 | Leakage test, dynamic (replaces the v1 static linter): perturb data after t and assert features at t are bit-identical; shuffled-label test; wired into tests for every signal and feature | `analytics/leakage.py`, `tests/unit/analytics/test_leakage.py` | 5 h | none | A deliberately leaky signal fails both tests |
| 2B.4 | Confidence calibration, held-out: fit isotonic mapping on seeds 0 to 99, report Brier and ECE on seeds 100 to 199; shipped only if calibrated Brier is no worse than raw; labelled "calibrated to synthetic generator <hash>" | `analytics/calibration.py`, `agents/base.py`, `evaluation.py` | 6 h | none | Reliability diagram in docs on held-out seeds; or the item is recorded as not shipped with the numbers |
| 2B.5 | Model registry and cards: hash, training data hash, metrics, date, label; same seed reproduces across OS and thread counts within tolerance; drift check (PSI on features) at load | `ml/registry.py`, `data/models/`, `docs/ml/` | 5 h | none | Cross-platform reproduction test in nightly; PSI above threshold raises a warning |

R2B: 36 h, about 4.5 plan-days, 2 wall-clock days. Dependencies: `scikit-learn` only (already an
extra). The regime HMM (v1 2B.6) is cut: R04 changes only the premium, so an HMM on market returns
cannot pass its own test. The GBM cost model (v1 2B.1) became the parametric model in 2T.8.

### R3E: evaluation that measures what it can and labels the rest. Branch `r3e-eval`. 0.6.0 part 5

Runs after R3A, R3B, R3C (request and response shapes settled).

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 3E.1 | Golden set governance: 30 investigation and 15 research questions with human-written expected findings (primary cause, must-mention evidence, must-not-claim); frozen with a version; changes only through a PR labelled `golden-change`; the owner signs off 10 sampled items (free, 20 min) | `tests/golden/*.json`, `docs/evaluation` | 6 h | none | Golden file versioned; owner sign-off recorded in the PR |
| 3E.2 | Evaluation taxonomy and report: capability (cause and verdict accuracy, power and false-positive rates from R2 and R2T), safety (invariants under adversarial clients), robustness (fault rungs), cost (local token estimate); every live-only metric (planner quality, real-model injection resistance, narrative faithfulness, refusal rate, latency, cache hit rate, run-to-run variance) printed as UNKNOWN / NOT RUN LIVE | `evaluation.py`, `cli.py`, `docs/evaluation` | 8 h | D15 | One command produces the table; no cell claims a live number |
| 3E.3 | Regression history: results appended to `docs/evaluation/history.csv` per release; a release note must explain any metric that moved beyond its run-to-run band; `scripts/check_docs.py` verifies every number in the docs against the command output and every documented CLI command runs | `evaluation.py`, `docs/evaluation/history.csv`, `scripts/check_docs.py`, `ci.yml` | 6 h | none | CI fails on a docs number mismatch; history has one row per release from 0.4.0 |
| 3E.4 | Drift monitors: cassette staleness (request hash), RAG recall floor, golden-set version, model pin file, SDK version vs cassettes (a Dependabot SDK bump fails replay until cassettes are re-authored) | `tests/contract/`, `ci.yml` | 4 h | none | Each monitor has a failing test fixture |

R3E: 24 h, about 3 plan-days, 1.5 wall-clock days. Tag 0.6.0 after R3A, R3B, R3C, R2B, R3E.

Cut from v1 and not replaced: judge LLM (3E.3 v1), live report (3E.4 v1), red-team LLM agent (3B.8
v1, replaced by `ObedientInjectionClient`), prompt-cache hit-rate test (3A.4 v1), batch runner (3A.7
v1), Files API (3C.6 v1, wrong on the facts), mid-conversation system messages (3B.5 v1, every call is
single-turn), signal-prototyping sandbox agent (3D v1, decision D13 reversed).

### R5: one preregistered real-data study. Branch `r5-real-data`. 0.7.0 (VOID under its preregistration)

Needs R2 (2.13 membership mask, 2.8 ledger), R4 (audit log). Free sources only. No CAPTCHA is ever
bypassed; if Stooq bulk download requires one, the owner downloads by hand or the study uses yfinance
only and says so.

| Id | Item | Files | Effort | Decision | Done when |
|---|---|---|---|---|---|
| 5.1 | Preregistration with power analysis, committed and hashed before any data is read. Hypothesis: 12-1 momentum (month-end signal `close[t-21]/close[t-252]`), equal-weight decile long-short, executed at the next day's close, one-month hold, excess returns, on an approximately point-in-time S&P 500. Formation from 2004-01; evaluation 2005-01 to 2023-12; one primary test: one-sided Newey-West (3 lags) t on monthly net returns at alpha 0.05 (single hypothesis, so not t >= 3). Minimum detectable Sharpe about 0.57 stated. Holdout 2024-01 to a frozen last date, labelled pseudo-holdout because factor returns are public. Allowed and forbidden claims listed | `docs/research/prereg_momentum_2026-11.md` | 4 h | D2 | File hash recorded in the audit log before `data/stooq.py` exists |
| 5.2 | Point-in-time universe: free public S&P 500 change history (GitHub CSV reconciled against the Wikipedia change table, CC BY-SA attribution kept); dated ticker map (FB to META, GOOG/GOOGL, BRK.B formatting, Stooq `.US` suffix); monthly coverage report; study void if coverage under 95 %; survivor-only universe run in parallel to measure the bias | `data/universe_pit.py`, `data/reference/sp500_changes.csv`, `data/reference/ticker_map.csv` | 14 h | none | Membership on any date reproducible; coverage table committed; attribution file present |
| 5.3 | Stooq daily connector with yfinance cross-check on returns (not levels; conventions differ); raw data cached locally and gitignored, never committed; data quality gate: stale runs, zero-volume days, duplicate dates, moves beyond 40 % cross-checked, split detection at known ex-dates (AAPL 2014, 2020), holiday and half-day alignment | `data/stooq.py`, `data/quality.py`, `scripts/download_daily.py` | 22 h | none | Return cross-check agrees within 10 bps on at least 99.5 % of name-days; quality report committed; known splits pass |
| 5.4 | Costs for real data: fixed half-spread schedule (5 bps from 2010, 10 bps 2005 to 2009); square-root impact Y = 0.7 at $100M gross; borrow 0.3 % general collateral plus 5 % for the bottom 5 % by market cap; Corwin-Schultz as a sensitivity only | `analytics/costs.py`, `analytics/backtest.py` | 5 h | none | Known-answer tests; sensitivity table |
| 5.5 | Pipeline gate: replicate against Ken French UMD; correlation at or above 0.5 required, otherwise the result is "pipeline suspect" and no verdict is issued; FF5 plus UMD regression; per-year and crash-month table (2009, Nov 2020); breakeven cost | `analytics/factor_check.py`, `data/reference/ff_factors.csv`, `docs/research` | 6 h | none | Correlation printed; gate blocks the verdict below 0.5 |
| 5.6 | Run through the full CEAP research pipeline on dataset `X01` (no ground truth); publish net Sharpe with 95 % CI, UMD correlation, minimum detectable Sharpe, and the NULL if that is the result. Allowed claim: "Net Sharpe X, 95 % CI [a, b], 2005 to 2023, approximately point-in-time S&P 500, free data; UMD correlation Y; cannot detect Sharpe below 0.57." Forbidden: PROMOTE, alpha, tradable, any dollar capacity | `data/research_scenarios.py`, `evaluation.py`, `docs/research/result_momentum_2026-11.md` | 5 h | none | Result document shows the platform's own proposal output and the preregistration hash |
| 5.7 | One look at the pseudo-holdout, descriptive only (sign agreement, CI overlap with the in-sample estimate); recorded SPENT; the code refuses to run on the holdout range again without a flag that writes to the audit log | `docs/research/result_momentum_2026-11.md`, `analytics/backtest.py` | 2 h | none | Holdout marked SPENT in the audit chain |

R5: 58 h, about 7 plan-days, 3 wall-clock days plus download time. Ships in 0.7.0 with RP paper trading.

### R6: documentation pass per release (owner rule), not one pass at the end

Every round PR includes its docs pass (README, LEARN, COOKBOOK, ARCHITECTURE, DIAGRAMS, threat model,
quant page, AI page, CHANGELOG), budgeted at 3 h per round: 27 h across nine rounds. The final 0.7.0
docs pass additionally works through the claims table in `REVIEW.md` section 5 (4 h) and replaces
every landing-page number with the new honest set (power, false-positive rate, degraded count,
UNKNOWN for live metrics, real-data verdict). `scripts/check_docs.py` (3E.3) enforces it.

## 3. Totals

| Round | Hours | Plan-days | Wall-clock days | Version |
|---|---|---|---|---|
| R0 foundations | 42.5 | 5.5 | 2 | 0.4.0 |
| R1 fail-closed | 45 | 5.5 | 2 | 0.4.0 |
| R2 quant | 81 | 10 | 4 | 0.4.0 |
| R2T TCA | 76 | 9.5 | 4 | 0.5.0 |
| R4 platform | 69 | 8.5 | 3.5 | 0.5.0 |
| R3A LLM | 59 | 7.5 | 3 | 0.6.0 |
| R3B agents | 86 | 11 | 5 | 0.6.0 |
| R3C RAG | 30 | 4 | 1.5 | 0.6.0 |
| R2B ML | 36 | 4.5 | 2 | 0.6.0 |
| R3E evaluation | 24 | 3 | 1.5 | 0.6.0 |
| R5 real data | 58 | 7 | 3 | 0.7.0 |
| R6 docs (spread) | 31 | 4 | spread | all |
| Rebase and conflict tax (10 %) | 64 | 8 | spread | all |
| **Total** | **701.5** | **88** | **about 26 to 30 working days, 6 to 7 calendar weeks** | |

Critical path: R0 → R1 (+ R2) → 0.4.0 → R3A → R3B → R3E → 0.6.0, about 32 serial plan-days. Beside it:
R2T and R4 → 0.5.0 → R2B, and R5 + RP → 0.7.0. Originally at most two Claude sessions at once (D17);
superseded on 2026-11-07 by the owner's max-parallel instruction (section 9). CI: about 40 cycles at 8 to 12 min. API spend $0. External spend $0.
Usage: check `get_usage` at every round report; finish the branch in flight before pausing; never
extra usage.

## 4. Order of execution

1. Commit `docs/review/2026-11-07/` on main as a docs-only commit.
2. R0 on `r0-foundations` (one session). Merge via PR once 0.5 is toggled by the owner; until then
   merge with `--merge` after CI green.
3. R1 on `r1-fail-closed` and R2 on `r2-quant`, combined in one PR. Tag 0.4.0 (shipped 2026-11-07, PR #1).
4. Parallel lanes: R2T on `r2t-tca`, R4 on `r4-platform`, the Ken French rf series. One combined PR,
   tag 0.5.0 (shipped 2026-11-08, PRs #2, #3).
5. Parallel lanes: R3A, then R3B (R3B builds on R3A); R3C after R3B's reporter change lands; R2B
   after R2T. Then R3E. One combined PR, tag 0.6.0.
6. R5 (VOID under its preregistration) and RP paper trading. Tag 0.7.0.
7. Update the memory note with what shipped and what did not.

## 5. File conflict map (why the order above)

| File | Rounds touching it | Owner order |
|---|---|---|
| `harness/engine.py` | R1, R4, R3A, R3B | R1 first; R4 and R3A only additive; R3B last |
| `llm/client.py` | R1, R3A, R3C | R1 pins and timeouts; R3A owns the rest; R3C adds the citations call |
| `agents/critic.py`, `agents/reporter.py` | R1, R3A, R3B, R3C | R1 ladder only; R3A schemas; R3B logic; R3C reporter two-call last |
| `evaluation.py` | R1, R2, R2T, R2B, R3E | each adds a suite; R3E owns the report |
| `analytics/execution_metrics.py`, `attribution.py` | R2T, R2B | R2T first |
| `policy/approvals.py` | R1, R4, R2T, R3B | R1 four-eyes; R4 persistence; R2T outlier queue; R3B payload |
| `tests/conftest.py` | R0 only | fixture refactor done once in R0 |
| `domain/*` | R0 only (contracts PR) | later changes need the `contracts-change` label |
| `requirements.lock` | any round adding a dependency | regenerated by the CI `lock-fresh` job, never by hand |

## 6. Edge cases the plan now covers (and where)

Restart mid-task (4.2), late approval decision (4.2), concurrent approvals (4.2), idempotency key
with different body (4.4), schema evolution of persisted results (0.7, 4.1), cassette drift on prompt
or SDK change (3A.5, 3E.4), test runtime (0.6), flaky subprocess tests (3B.9 marked slow), Windows vs
Linux newline hashing (0.3), host timezone vs data timezone (2.4), DST (2.4), half days and special
closures (2.4), delistings with two bounds (2.5), splits and ticker changes (5.2, 5.3), stale and
zero-volume and duplicate prints (5.3), LULD and auction prints and late prints (2T.1), Reg SHO SSR
(2.3), financing and excess returns (2.3), PII in questions (0.8), secrets in cassettes (0.8), licence
of data sources (5.2, 0.4 allowlist), lock regeneration on Linux only (0.2), thinking tokens
truncating output (1.9), refusal stop reason (1.5), hung model call (1.9), model retirement (1.9,
0.4.0 deadline), SQLite under asyncio and Windows file locks (4.1), backups and retention (4.1),
multiple testing across everything the agent can search (2.8, 2.10), power of the real-data test
(5.1), survivorship (5.2), circular ground truth (2.9), seasonality confounding the baseline (2T.2).

## 7. What this plan will not deliver

No alpha, no tradable strategy, no desk-ready product, no claim about live model quality. After
0.7.0 the honest description is: a fail-closed, auditable, restart-safe research and TCA pipeline
whose harness guarantees are proven against adversarial fake models, whose quant is correct under
realistic execution and whose hurdles come from measured null distributions, demonstrated on
realistic synthetic data and one preregistered, power-analysed real-data test with free data. Live
model behaviour is labelled UNKNOWN until someone funds a capped run; the moment that happens, only
decision D15 changes.

## 8. Risk register

| # | Risk | L | I | Mitigation |
|---|---|---|---|---|
| 1 | Merge conflicts on `engine.py`, `client.py`, `evaluation.py` | 5 | 4 | Contracts PR (0.7); serial R3A → R3B → R3E; conflict map (section 5) |
| 2 | Lock regeneration impossible on Windows | 5 | 4 | `uv --universal` in CI (0.2) |
| 3 | CI runtime past 15 min | 4 | 3 | Tiers, xdist, nightly (0.6) |
| 4 | Live-dependent acceptance makes "done" fiction | 5 | 4 | Every criterion offline; UNKNOWN labels (3E.2) |
| 5 | Persisted schema churn | 4 | 4 | `user_version` migrations, `schema_version` on JSON (0.7, 4.1) |
| 6 | Usage limit mid-round | 4 | 4 | One release per round; finish the branch before pausing |
| 7 | Owner review bottleneck | 5 | 3 | Two sessions max; diff summaries; formula checklists |
| 8 | Secrets or PII in cassettes | 3 | 5 | Scrubber, gitleaks, redaction (0.8) |
| 9 | Data licence or ToS problem | 3 | 3 | No raw data committed; attribution; allowlist |
| 10 | Ground-truth churn hides regressions | 4 | 4 | Dedicated re-baseline PRs with diff tables (2.11, 2T.2) |
| 11 | Fixture rework cascades | 5 | 3 | Done once in R0 (0.6) |
| 12 | Model id retires before 0.4.0 ships | 3 | 4 | 0.4.0 deadline 2026-11-29; 1.9 in R1 |

## 9. Decision log (all decided by the maintainer on 2026-11-07 under the owner's delegation)

- **D1** (a): new `approver` role; `admin` loses `approvals:decide`; staging approver must differ from proposal approver. Reason: clean separation in the audit trail; T17 becomes true.
- **D2** (a): run the real-data study on Stooq and yfinance only, no sign-ups, no card, no CAPTCHA bypass, published whatever it says. Reason: the only route out of "demo that always says PROMOTE"; zero cost.
- **D3** (a): all rounds, in the order of section 4, one minor release per round (see D16; rounds were later combined per release by owner instruction). Reason: owner asked for everything improved; smaller releases roll back.
- **D4** revised: the PROMOTE hurdle is not fixed at t >= 3. It is chosen from the null sweep (2.10) as the lowest t whose false-PROMOTE upper bound is at most 5 % over the full search the agent can run, with deflated Sharpe over ledger-counted trials. The single preregistered hypothesis in R5 uses a one-sided Newey-West t at alpha 0.05, because t >= 3 is a bar for factors mined from a zoo. Reason: v1 chose the hurdle before measuring and would have made PROMOTE unreachable at realistic premia over a 2-year in-sample window.
- **D5** (a): scenarios that flip to REJECT under t+1 execution stay REJECT; ground truth comes from generator parameters. Reason: honest and non-circular.
- **D6** revised: no dense embeddings in 0.x. BM25 with a measured chunking strategy and a held-out retrieval eval. Reason: torch in the lock and a weight download in CI are not worth it for twenty documents.
- **D7** (a): SQLite from the standard library, single writer thread, WAL, forward-only migrations, backup before migrate. Reason: zero dependencies; Postgres path kept.
- **D8** (a): branch protection with required checks and 0 required approvals; tag-triggered release workflow that refuses red SHAs. Reason: three releases shipped red; a solo maintainer must not be locked out.
- **D9** (a): "bank-style governance pattern, demonstration scale"; recovery numbers under "plumbing tests"; live metrics shown as UNKNOWN. Reason: what the code supports.
- **D10** (a): bounded case memory, harness-written, append-only, OFF in evaluation, partitioned by dataset, model read-only. Reason: learning across investigations without contaminating the eval or letting the model write state.
- **D11** revised: parametric fitted square-root pre-trade model with conformal intervals (2T.8), not gradient boosting. Reason: on synthetic data a GBM learns the generator; a parametric model with shift-tested coverage is honest and interpretable.
- **D12** revised: ridge combination only, with purged CV and ledger-counted trials (2B.2). Gradient boosting and meta-labeling dropped. Reason: three features on thirty names do not justify them.
- **D13** reversed: no signal-prototyping agent, no sandboxed model-written code in 0.x. Reason: a subprocess with an import allowlist is not a sandbox (numpy and pandas are escape surfaces, no seccomp on Windows), it cannot be exercised without live calls, and it is the largest multiple-testing engine in the project.
- **D14** (a): Opus 5.5 planner, Sonnet 5.5 narrative and critique, Haiku 5.5 reserved for judge paths, all as pinned configuration exercised only through cassettes and contract tests. Reason: the 0.3.1 live run showed planner quality drives outcomes.
- **D15** (b): no live model calls. Every model-dependent path is proven offline by wire contract tests, adversarial fake clients, harness invariants and provenance-tagged cassettes; anything that needs a real model is labelled UNKNOWN / NOT RUN LIVE. Reason: owner constraint of zero spend. If the owner later funds a capped run, D15 becomes (a) and nothing else changes.
- **D16** revised 2026-11-07 (owner: maximum parallelism, one combined PR per release): release train 0.4.0 (R0+R1+R2, shipped 2026-11-07), 0.5.0 (R2T+R4+Ken French rf, shipped 2026-11-08, PRs #2, #3), 0.6.0 (R3A, R3B, R3C, R3E, R2B), 0.7.0 (R5, VOID, + RP paper trading); 1.0.0-rc only when API and database schema are frozen. Reason: fifteen breaking changes in one 0.4.0 cannot be rolled back.
- **D17** new: at most two Claude sessions in parallel. Reason: the owner reviews about two substantial diffs a day; three sessions outrun the reviewer and inflate the conflict tax. **Superseded** on 2026-11-07 by the owner's max-parallel instruction (as many lanes as useful, one combined PR per release).

### Owner decisions of 2026-11-08

- **D18** golden-set sign-off: the owner's sign-off of 10 sampled golden-set items is done after the 0.6.0 tag; until then `latest.json` reports it as PENDING.
- **D19** no live-model run: D15 stays, zero spend. Every live-only metric stays UNKNOWN / NOT RUN LIVE.
- **D20** R5 stays VOID. It is rerun only if the owner hand-downloads the Stooq bulk file, and then under a new (v2) preregistration.
- **D21** no 1.0.0-rc1 yet. Fixes ship as 0.7.x; revisit after 2 to 4 weeks of paper-trading log.
- **D22** paper trading is enabled after 0.7.0.
