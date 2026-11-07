# CEAP v0.4 plan: itemised, with effort, cost and owner decisions

Date: 2026-11-07. Status: SAVED, NOT STARTED. Execution begins only on the owner's word.
All decisions D1 to D15 were delegated to the maintainer on 2026-11-07 under two constraints: no money
spent (including no API spend) and no card details given anywhere. They are recorded in section 6.
The AI work is specified in `AI_PLAN.md` (rounds 2B, 3A to 3E), which replaces Round 3 below.
Review this plan answers: `REVIEW.md` in this folder.

## How to read the tables

- **Effort** is engineering hours of focused session work. Rounds roll up to plan-days (8 h) and to
  wall-clock days at the calibration used across these repos: 2 to 3 parallel sessions deliver about
  12 to 15 plan-days per wall-clock day. Reviewer time and CI time are stated separately.
- **Cost** has three parts. *API spend* in USD for real-model calls (zero unless stated; every live
  run has a hard cap and uses the key already in the gitignored `.env`). *Session budget* is the
  Claude usage this work consumes, given as S (small, under an hour of a session), M (one to three
  hours), L (half a day or more). *External* covers data downloads, sign-ups and purchases, which
  are always zero here: free sources only, nothing that asks for a card.
- **Owner decision** is `none` when the maintainer can decide under the delegation already granted,
  or a decision id `D1` to `D9` listed in section 4. An item with a decision id does not start until
  that decision is recorded.
- **Done when** is the acceptance test. If it cannot be shown, the item is not done.

Standing rules applied throughout: one batch PR per round; full docs review before each round PR;
never the word the banned job-prep word in public files; zero spend and no card-based sign-ups; local tests
finished before any push; CI confirmed green on the actual run before a release is called done.

## 1. Goal and non-goals

Goal for v0.4.0: the project tells the truth at every layer. Governance cannot be bypassed, no
degradation is silent, the quant is correct under realistic execution timing, the evaluation
reports false positives alongside recovery, a real-model evaluation exists and is replayable in CI,
state survives a restart, and one preregistered real-data study has been run and reported whatever
it says.

Non-goals for v0.4: live trading, OMS/EMS connectors, a UI, SSO, paid data, trained ML models. These
remain listed under "what is not here". Version stays 0.x until something has run on real data.

## 2. Rounds and items

Each round is one branch, one PR, CI green, merged with `--merge`, docs pass included. Round 1 lands
first. Rounds 2, 3 and 4 touch disjoint files and run in parallel sessions. Round 5 needs Round 2.
Round 6 is last.

### Round 1: fail closed by default. Branch `r1-fail-closed`

| Id | Item | Files | Effort | Cost | Owner decision | Done when |
|---|---|---|---|---|---|---|
| 1.1 | `build_router` raises `SettingsError` when provider is `anthropic` and the client cannot be built; mock only when provider is explicitly `mock` | `llm/router.py`, `config.py` | 2 h | S, $0 | none | Test: anthropic provider with no SDK fails at startup; API never reports `ok` on a mock it did not ask for |
| 1.2 | Dockerfile installs `.[llm]` so the image can reach a real model | `Dockerfile` | 0.5 h | S, $0 | none | `docker build` then `python -c "import anthropic"` succeeds in the image |
| 1.3 | Four-eyes: `decide` rejects `decided_by == requested_by`; new `approver` role holds `approvals:decide` | `policy/approvals.py`, `policy/permissions.py`, `api/routes.py`, threat model T17 | 4 h | S, $0 | **D1** | Adversarial test: same principal cannot request and approve; two distinct principals can |
| 1.4 | Harness default gateway becomes deny; auto-approve only in `dev`; non-dev with `CEAP_AUTO_APPROVE=true` refuses to serve | `harness/engine.py`, `api/app.py`, `config.py` | 2 h | S, $0 | none | Constructing `AgentHarness()` without a gateway denies every approval; serving test fails fast |
| 1.5 | New result status `DEGRADED` plus a `warnings` list; planner canonical fallback, critic LLM exception, runtime router fallback and `stop_reason == max_tokens` all set it | `domain/tasks.py`, `agents/planner.py`, `agents/critic.py`, `harness/engine.py`, `llm/client.py`, `evaluation.py`, `cli.py`, `api/models.py` | 6 h | M, $0 | none | Each path has a test that asserts `DEGRADED` and the warning text; evaluation prints the degraded count |
| 1.6 | Required specialist agents: harness appends any of market, execution, quant, risk, engineering a plan omitted; a failed agent yields `INCOMPLETE_ANALYSIS`; `primary` has no default | `harness/engine.py`, `agents/reporter.py`, `llm/client.py` | 4 h | S, $0 | none | Plan missing `quant` still runs quant; forced agent failure never produces "no material anomaly" |
| 1.7 | MCP adapter: missing `readOnlyHint` means not read-only, missing `riskLevel` means HIGH; `trusted_servers` allowlist in settings, untrusted servers are read-only regardless of hints | `mcp/adapter.py`, `mcp/client.py`, `config.py`, `policy/engine.py` | 4 h | S, $0 | none | Test: an unannotated tool from an untrusted server is denied for any write |
| 1.8 | Standalone FastMCP servers enforce `required_capabilities` from an env-provided principal | `mcp/server.py`, each `mcp/*/__main__.py` | 3 h | S, $0 | none | stdio test: `stage_orders` without `trading:execute` returns a policy error |
| 1.9 | Per-call LLM timeout (`CEAP_LLM_TIMEOUT_S`, default 60); LLM calls wrapped in `race_cancellation`; `LLMRequest.temperature` removed (current models reject sampling parameters); default model ids moved off the deprecated `claude-sonnet-4-5` (retires 2026-11-30) to `claude-opus-5-5` planner, `claude-sonnet-5-5` narrative and critique, `claude-haiku-5-5` judges | `llm/client.py`, `harness/engine.py`, `llm/models.py` | 3 h | S, $0 | none | Hung mock client falls back within the timeout; cancel during a planner call returns in under 1 s; `git grep sonnet-4-5` empty |
| 1.10 | Public hygiene: remove the banned job-prep word from README, LEARN (section 17 retitled "Knowledge check"), docs/INDEX; `git rm "Claude outputs/ci.yml"`; README banner stating all data is synthetic and nothing trades; fix `.venv\Scripts` escaping | `README.md`, `LEARN.md`, `docs/INDEX.md`, repo root | 1 h | S, $0 | none | the banned-word check (`scripts/check_banned_words.py`) returns nothing; banner in the first screen of README |
| 1.11 | Tests for every item above (about 15 new) and the threat-model entries T17, T20 (untrusted MCP), T21 (construction-time fallback) | `tests/adversarial`, `tests/agent`, `docs/threat-model` | 4 h | M, $0 | none | Suite green locally, then CI green on the push |

Round 1 roll-up: 33.5 h = about 4 plan-days. Wall-clock about 1 day with two sessions. CI: one cycle
of about 4 min. Reviewer: a second session reads the policy diff (30 min). API spend $0.

### Round 2: quant correctness. Branch `r2-quant`

| Id | Item | Files | Effort | Cost | Owner decision | Done when |
|---|---|---|---|---|---|---|
| 2.1 | Execution at t+1: `execution_lag_days` in `BacktestSpec` (default 1); fill at next close, option next open | `analytics/backtest.py`, `domain/research.py`, `mcp/backtest/server.py` | 4 h | S, $0 | none | Lag-sensitivity test exists; reversal R03 under lag 1 reproduces the reviewer's sign flip |
| 2.2 | Weight drift between rebalances; turnover from drifted to target weights | `analytics/backtest.py` | 3 h | S, $0 | none | Known-answer test on a two-name, three-day case |
| 2.3 | Borrow cost by liquidity tier (A 0.5 %, B 2 %, C 10 % per year, documented as assumptions); short financing line in the cost breakdown | `analytics/backtest.py`, `data/historical.py`, `docs/quant/quant.md` | 3 h | S, $0 | none | Long-short backtest reports borrow separately; long-only reports zero |
| 2.4 | Exchange holiday calendar (static NYSE list 2010 to 2030 committed; no new dependency) | `data/historical.py`, `analytics/backtest.py` | 2 h | S, $0 | none | No bars on Thanksgiving or Good Friday in generated data |
| 2.5 | `adv_20` lagged one day; NaN and delisting policy (hold last price or -100 % option); per-name position and participation caps inside the loop | `data/historical.py`, `analytics/backtest.py` | 4 h | S, $0 | none | Tests for each; cap breach is clipped and recorded, not silently traded |
| 2.6 | Capacity sweep over notional {10, 25, 50, 100, 250}M reported with the backtest | `analytics/backtest.py`, `domain/research.py`, `agents/backtest.py` | 3 h | S, $0 | none | Net Sharpe declines monotonically with notional on the cost-drag scenario |
| 2.7 | Statistics: gate on non-overlapping horizon-h IC matching the holding period; Newey-West t-stat for overlapping series; `ic_ir_annualised`; rename signal turnover to `rank_autocorrelation_turnover`; drop last h in-sample dates from in-sample stats | `analytics/signal_statistics.py`, `domain/research.py`, `agents/alpha.py` | 6 h | M, $0 | none | Null test over 500 seeds: |t| > 2 in about 5 % of cases, not 0 % or 20 % |
| 2.8 | Multiple testing: `n_trials` in `ResearchScope`, incremented per distinct parameter set; deflated Sharpe; PROMOTE hurdle t >= 3.0; `OVERFIT` renamed `OOS_DECAY`; new `REGIME_BREAK`; `in_sample_end` locked per dataset | `analytics/research_assessment.py`, `domain/research.py`, `data/research_scenarios.py`, `agents/critic.py` | 6 h | M, $0 | **D4** | Agent cannot move the split; two trials on one dataset raise the hurdle visibly in the proposal |
| 2.9 | Risk: beta against the market excluding the name with shrinkage toward 1; stress adds sector shock, short-squeeze tail (+20 % on shorts) and liquidation cost at 10 % ADV; limits evaluated over the full OOS history | `analytics/portfolio_risk.py`, `mcp/risk/server.py`, `agents/portfolio_risk.py` | 6 h | M, $0 | none | Known-answer tests; worst-date limit breach reported, not just the as-of date |
| 2.10 | Fix `square_root_impact_estimate_bps` units to sigma_daily * sqrt(Q/ADV) and wire it in as the pre-trade expected cost in TCA | `analytics/market_impact.py`, `analytics/execution_metrics.py` | 2 h | S, $0 | none | Known-answer test; T08 shows expected vs realised impact side by side |
| 2.11 | Realistic planted premia (target Sharpe 0.3 to 0.8) with decay and one crash scenario; near-threshold scenarios; pure-noise set | `data/historical.py`, `data/research_scenarios.py` | 6 h | M, $0 | none | At least two scenarios land within one standard error of a gate |
| 2.12 | Null sweep: 200 seeds of the no-alpha template; false-PROMOTE rate published; target at most 5 % | `evaluation.py`, `cli.py`, `tests/evaluation` | 3 h | M, $0 | none | `ceap evaluate --suite null` prints the rate; CI runs a 20-seed subset |
| 2.13 | Re-derive all 21 ground truths under t+1 execution; reversal scenarios keep whatever the honest numbers say | `data/research_scenarios.py`, `docs/evaluation` | 3 h | S, $0 | **D5** | Every `expected_verdict` matches a fresh run; the change log records which flipped |
| 2.14 | TCA session: America/New_York 09:30 to 16:00, opening and closing auction prints flagged, U-shaped volume, passive and aggressive fills, SELL parents, odd lots | `data/synthetic.py`, `data/scenarios.py`, `analytics/vwap.py` | 8 h | M, $0 | none | Timezone test; sell-side IS test; auction prints excluded from interval VWAP by default |
| 2.15 | Perold IS with delay cost (decision time before arrival) and explicit fees and rebates | `analytics/implementation_shortfall.py`, `analytics/execution_metrics.py`, `data/synthetic.py` | 4 h | S, $0 | none | Components sum to total IS in a test to 1e-9 |
| 2.16 | Markouts at 1 s, 10 s, 60 s, 5 min, beta-adjusted against a synthetic index; size-weighted effective and realised spread; price improvement; venue stats conditioned on order type | `analytics/slippage.py`, `analytics/market_impact.py`, `analytics/execution_metrics.py`, `mcp/execution/server.py` | 8 h | M, $0 | none | Known-answer tests; T01 "impact" now reads near zero instead of -45 bps |
| 2.17 | Per-order IS confidence band (bootstrap over fills); deterioration trigger in standard errors; attribution scores shown next to a bps decomposition | `analytics/attribution.py`, `analytics/execution_metrics.py`, `agents/quant.py` | 6 h | M, $0 | none | T01 no longer flags deterioration; T05 to T10 still find their cause |
| 2.18 | Re-run the 50 execution scenarios; publish the new accuracy honestly, including failures; docs for every new formula with a worked example | `evaluation.py`, `docs/quant/quant.md`, `docs/evaluation` | 4 h | M, $0 | none | Numbers in docs match `ceap evaluate --suite all` output byte for byte |

Round 2 roll-up: 81 h = about 10 plan-days. Wall-clock about 3 to 4 days with two sessions on it.
CI: two cycles. Reviewer: a separate session re-derives every formula from the diff before merge
(2 h). API spend $0.

### Round 3: AI layer. Superseded by `AI_PLAN.md`

The AI work is now six branches specified item by item in `AI_PLAN.md`: 3A LLM layer (structured
outputs, model ids, caching, cost records), 3B agents and agentic AI (planner owns scope, hypothesis
table, critic with tools, evidence binding, calibration, case memory, red team), 3C RAG (BM25,
retrieval eval, citations, Files API), 2B ML (pre-trade cost model, anomaly and change-point
detection, causal attribution, purged-CV signal combination, regime HMM, leakage linter, model
registry), 3D signal-prototyping agent (sandboxed, gated), 3E evaluation (cassettes, golden set,
judge, live report, regression history). Totals: 226 h, about 29.5 plan-days, about 6 wall-clock days
with three sessions. API spend $0 by default (decision D15).

### Round 4: platform and controls. Branch `r4-platform`

| Id | Item | Files | Effort | Cost | Owner decision | Done when |
|---|---|---|---|---|---|---|
| 4.1 | Persistence: SQLite via the standard library; tables for tasks, results, approvals, staged orders, LLM calls; append-only `audit_log` with a hash chain; repositories swap in behind the existing interfaces | `data/repositories.py`, `domain/repositories.py`, `platform.py`, `policy/approvals.py`, `mcp/execution/staging.py` | 10 h | L, $0 | **D7** | Restart test: a pending approval survives a process restart and can be decided; audit chain verifies |
| 4.2 | Ownership scoping on all reads; `admin` and new `auditor` may read all | `api/routes.py`, `policy/permissions.py` | 3 h | S, $0 | none | Viewer cannot read another principal's task; auditor can |
| 4.3 | Release the concurrency semaphore while awaiting approval; `CEAP_APPROVAL_TIMEOUT_S`; idempotency key header on POST; `gross_notional` upper bound; `paginate` clamps negatives | `api/routes.py`, `policy/approvals.py`, `api/models.py`, `mcp/common.py`, `config.py` | 5 h | M, $0 | none | Four pending approvals do not block a fifth investigation; duplicate POST returns the same task id |
| 4.4 | Approval payload hash bound to the staged portfolio; `stage_orders` recomputes, hashes and refuses on mismatch | `mcp/execution/server.py`, `mcp/execution/staging.py`, `harness/engine.py` | 4 h | S, $0 | none | Test mutates the dataset between approval and staging; staging refuses |
| 4.5 | Auth: keys stored as salted hashes; optional expiry; correlation id per request; `/health` reduced to liveness; `/metrics` behind a key; duplicate-key error no longer prints the key | `api/auth.py`, `config.py`, `api/routes.py`, `observability/logging.py` | 5 h | M, $0 | none | Raw key never appears in logs or errors; every log line carries the request id |
| 4.6 | CI: PR-required branch protection (owner action), matrix 3.11 and 3.12, pip cache, coverage floor 85 %, `pip-audit`, actions pinned to SHAs, `permissions: contents: read`, concurrency group, Docker build and `--help` smoke, hashed `requirements.lock` generated on Linux in CI, Dependabot | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `requirements.lock` | 6 h | M, $0 | **D8** | CI green on both Pythons with coverage shown; lock has hashes and needs no hand edits |
| 4.7 | Docker: Python 3.12, non-root user, install from the hashed lock with `.[llm]`, HEALTHCHECK, `.dockerignore`, digest-pinned base | `Dockerfile`, `.dockerignore` | 2 h | S, $0 | none | Image builds in CI and `ceap --help` runs as non-root |
| 4.8 | Tests: restart-mid-approval, idempotency, ownership, property-based analytics invariants with `hypothesis`, MCP schema contract tests, golden report snapshots | `tests/**` | 8 h | M, $0 | none | About 40 new tests; `hypothesis` finds no counterexample in 1000 cases per invariant |
| 4.9 | Prometheus exposition fixed (no `_max`/`_min` under summary); HTTP latency and error metrics; OpenTelemetry path covered by one test | `observability/metrics.py`, `observability/tracing.py`, `api/app.py` | 3 h | S, $0 | none | `promtool check metrics` passes on the output |

Round 4 roll-up: 46 h = about 6 plan-days. Wall-clock about 2 to 3 days with two sessions. CI: two
cycles plus the first matrix run. Owner action: branch protection in GitHub settings (5 min). API
spend $0.

### Round 5: one preregistered real-data study. Branch `r5-real-data`

Free sources only. No sign-ups. Decision **D2** gates the whole round.

| Id | Item | Files | Effort | Cost | Owner decision | Done when |
|---|---|---|---|---|---|---|
| 5.1 | Preregistration committed before any data is read: hypothesis "12-1 momentum, decile long-short, monthly rebalance, t+1 close execution, OOS Sharpe > 0 net of estimated costs on a point-in-time large-cap US universe 2010 to 2023, with 2024 to 2026 as the locked holdout". One hypothesis, one holdout, one look | `docs/research/prereg_momentum_2026-11.md` | 2 h | S, $0 | **D2** | File committed with a hash before `ceap.data.stooq` exists |
| 5.2 | Point-in-time S&P 500 membership from free public change histories, with an explicit gap note; delisting returns absent, stated as a bias toward optimism | `data/universe_pit.py`, `data/reference/sp500_changes.csv` | 6 h | M, $0 | none | Membership on any date between 2010 and 2026 is reproducible from the committed file |
| 5.3 | Stooq daily connector with yfinance cross-check on 20 names; adjusted closes; download cached locally and gitignored | `data/stooq.py`, `scripts/download_daily.py` | 5 h | M, $0 (about 1 GB download, 30 to 60 min) | none | Cross-check agrees within 0.1 % on 95 % of adjusted closes |
| 5.4 | Costs for real data: Corwin-Schultz spread from high and low, tiered borrow assumptions, square-root impact at a stated notional | `analytics/costs.py`, `analytics/backtest.py` | 4 h | S, $0 | none | Known-answer test on the Corwin-Schultz formula |
| 5.5 | Run the full CEAP research pipeline on dataset `X01` (no ground truth) so the platform itself produces the verdict; publish verdict, deflated Sharpe, cost share and the NULL if that is the result | `data/research_scenarios.py`, `evaluation.py`, `docs/research/result_momentum_2026-11.md` | 4 h | M, $0 | none | Result document shows the platform's own proposal output and the preregistration hash |
| 5.6 | One look at the holdout, recorded as spent in the docs; no second look ever | `docs/research/result_momentum_2026-11.md` | 1 h | S, $0 | none | Holdout marked SPENT; code refuses to run on the holdout range again without a flag that writes to the audit log |

Round 5 roll-up: 22 h = about 3 plan-days. Wall-clock about 2 days plus download time. CI: one cycle.
API spend $0 (mock LLM is fine here; the quant is the point). The likely outcome is NULL or a weak
positive sign, and the document says so before the run.

### Round 6: documentation pass and release v0.4.0. Branch `r6-docs-release`

| Id | Item | Files | Effort | Cost | Owner decision | Done when |
|---|---|---|---|---|---|---|
| 6.1 | Claims table from `REVIEW.md` section 5 worked line by line: every public number replaced by the new honest set (recovery rate, false-positive rate, degraded count, live-eval latency and cost, real-data verdict) | `README.md`, `docs/index.html`, `docs/GITHUB_PAGES.md` | 4 h | M, $0 | **D9** | No number on the landing page lacks a command that reproduces it |
| 6.2 | LEARN, COOKBOOK, ARCHITECTURE, DIAGRAMS, threat model, quant page, AI page updated for every round; new "what changed and why" section per round | `LEARN.md`, `COOKBOOK.md`, `docs/**` | 6 h | M, $0 | none | Second session reads every doc against the code and finds no drift |
| 6.3 | CHANGELOG 0.4.0 with the honest story including which scenarios flipped and what the real-data study said | `CHANGELOG.md`, `pyproject.toml`, `src/ceap/__init__.py` | 1 h | S, $0 | none | Version in three places agrees |
| 6.4 | Release: PR merged, CI green on main confirmed by run id, tag `v0.4.0`, `gh release create` | GitHub | 1 h | S, $0 | none | Release notes link the CI run; the banned-word check (`scripts/check_banned_words.py`) empty |

Round 6 roll-up: 12 h = about 1.5 plan-days. Wall-clock about 1 day. CI: one cycle.

## 3. Totals

| Round | Hours | Plan-days | Wall-clock days | API spend | Owner decisions |
|---|---|---|---|---|---|
| 1 fail-closed | 33.5 | 4 | 1 | $0 | D1 |
| 2 quant | 81 | 10 | 3 to 4 | $0 | D4, D5 |
| 2B ML (AI_PLAN) | 53 | 7 | 2.5 | $0 | D11, D12 |
| 3A LLM (AI_PLAN) | 34 | 4 | 1.5 | $0 | D14 |
| 3B agents (AI_PLAN) | 62 | 8 | 3 | $0 | D10 |
| 3C RAG (AI_PLAN) | 29 | 4 | 1.5 | $0 | D6 |
| 3D prototyper (AI_PLAN) | 19 | 2.5 | 1 | $0 | D13 |
| 3E evaluation (AI_PLAN) | 29 | 4 | 1.5 | $0 | D15 |
| 4 platform | 46 | 6 | 2 to 3 | $0 | D7, D8 |
| 5 real data | 22 | 3 | 2 | $0 | D2 |
| 6 docs, release | 12 | 1.5 | 1 | $0 | D9 |
| **Total** | **420.5** | **52.5** | **about 14 to 16** (2B, 3x, 4 in parallel) | **$0** | 15 |

Separately: about 16 CI cycles at 4 min each; reviewer sessions about 8 h in total; owner actions
about 10 min (branch protection in GitHub settings). External spend: zero. API spend: zero; every
live-model item is built to run against recorded or hand-built cassettes and is marked NOT RUN LIVE in
the docs until the owner explicitly funds a capped run. Session budget: roughly 20 L-sized or 55
M-sized sessions; check `get_usage` at every round report and pause before the weekly limit, never
into extra usage.

## 4. Owner decisions

| Id | Decision | Options | Recommendation | Blocks |
|---|---|---|---|---|
| D1 | How to realise four-eyes | (a) new `approver` role, `approvals:decide` removed from `admin`; (b) keep admin, require two distinct admin principals | (a): clearer audit trail and matches how desks separate proposer and approver | 1.3 |
| D2 | Run the real-data study | (a) yes, free sources, published whatever it says; (b) skip for v0.4 | (a): it is the only way the project stops being a demo that always says PROMOTE | Round 5 |
| D3 | Scope of v0.4 | (a) all six rounds; (b) rounds 1, 2, 6 only (correctness first, AI and platform later) | (a) if the usage budget allows; (b) is the honest minimum | ordering |
| D4 | PROMOTE hurdle | (a) t >= 3.0 with deflated Sharpe (Harvey-Liu-Zhu style); (b) keep t >= 2.0 and only report the adjusted figures | (a): anything less and the null sweep will show a false-PROMOTE rate a desk would not accept | 2.8 |
| D5 | Reversal scenarios that flip to REJECT under t+1 execution | (a) keep them as REJECT ground truth; (b) redesign the planted premium so reversal survives | (a): a REJECT scenario that used to pass is a better teaching case than a rebuilt pass | 2.13 |
| D6 | Local embeddings extra | (a) add behind an extra with the eval; (b) BM25 only | (a) only if the eval shows a gain; the 130 MB weight download is the only cost | 3.8 |
| D7 | Persistence backend | (a) SQLite in the standard library; (b) Postgres behind an extra | (a): zero new dependencies, good enough for a single-node deployment, upgrade path kept | 4.1 |
| D8 | Branch protection on `main` requiring a PR and green CI | (a) enable; (b) keep committing to main | (a): three releases shipped red because nothing stopped them | 4.6 |
| D9 | Public positioning | (a) replace "enterprise-style" with "bank-style governance pattern, demonstration scale" and move recovery numbers under a "plumbing tests" heading; (b) keep current wording | (a) | 6.1 |
| D10 | Case memory (AI_PLAN 3B.7) | (a) bounded, harness-written, audited store, model read-only; (b) none | (a) | 3B.7 |
| D11 | Pre-trade cost model library (AI_PLAN 2B.1) | (a) scikit-learn HistGradientBoosting; (b) LightGBM | (a) | 2B.1 |
| D12 | ML signal combination (AI_PLAN 2B.5) | (a) include with purged CV and deflated Sharpe; (b) defer | (a) | 2B.5 |
| D13 | Signal-prototyping agent (AI_PLAN 3D) | (a) gated experiment, sandbox, human approval; (b) model never writes code | (a) built and tested with the mock and cassettes; never run live without funding | 3D |
| D14 | Model routing (AI_PLAN 3A.1) | (a) Opus 5.5 planner, Sonnet 5.5 narrative and critique, Haiku 5.5 judges; (b) Sonnet 5.5 everywhere | (a) as configuration; exercised only through cassettes | 3A.1 |
| D15 | Live evaluation cadence (AI_PLAN 3E.4) | (a) one batched live run per round, $2 cap; (b) no live runs; cassettes and mock only | (b) under the no-spend constraint; flips to (a) the day the owner funds it, no other change needed | 3E.4 |

## 5. Order of execution when started

1. Record D1 to D9 in this file (section 6) and commit this folder.
2. Round 1 in one session. Merge. Confirm CI green by run id.
3. Rounds 2, 3, 4 in three parallel sessions on three branches. Each ends with its own PR. Merge in
   the order they go green; rebase the others.
4. Round 5 after Round 2 is merged, if D2 is yes.
5. Round 6. Tag v0.4.0.
6. Update the memory note `ceap-review-2026-11-07` with what shipped and what did not.

## 6. Decision log

All decided by the maintainer on 2026-11-07 under the owner's delegation ("you decide for me; no money
to be spent, no card details to be given").

- D1: (a) new `approver` role; `approvals:decide` removed from `admin`. Reason: clean separation of proposer and approver in the audit trail.
- D2: (a) run the real-data study on Stooq and yfinance only, no sign-ups, no card, published whatever it says. Reason: the only way the project stops being a demo that always says PROMOTE; zero cost.
- D3: (a) all rounds, in the order of section 5. Reason: the owner asked for everything improved; usage is paced by `get_usage` checks.
- D4: (a) PROMOTE hurdle t >= 3.0 with deflated Sharpe. Reason: a desk would not accept the false-PROMOTE rate at t >= 2 across three signals and a parameter grid.
- D5: (a) scenarios that flip to REJECT under t+1 execution stay REJECT. Reason: a formerly passing case that now fails honestly is the better teaching case.
- D6: (a) local embeddings behind an extra, default BM25 unless the committed eval shows a gain. Reason: free weights, measured not assumed.
- D7: (a) SQLite from the standard library. Reason: zero dependencies, single-node is the deployment, Postgres path kept.
- D8: (a) branch protection requiring a PR and green CI. Reason: three releases shipped red because nothing stopped them. Owner action: toggle the setting (no cost).
- D9: (a) "bank-style governance pattern, demonstration scale"; recovery numbers under "plumbing tests". Reason: it is what the code supports.
- D10: (a) bounded case memory, append-only, harness-written, model read-only. Reason: adds real learning across investigations without letting the model write state.
- D11: (a) scikit-learn HistGradientBoosting. Reason: already an optional extra; monotone constraints supported; no new dependency.
- D12: (a) include the ML signal combination with purged CV and deflated Sharpe. Reason: fully testable on synthetic data and it makes the "ML" in the project real.
- D13: (a) build the prototyping agent as a gated experiment with sandbox and human approval; exercised with the mock and hand-built cassettes only. Reason: the safeguards are the point; no live spend.
- D14: (a) Opus 5.5 planner, Sonnet 5.5 narrative and critique, Haiku 5.5 judges, as configuration. Reason: the 0.3.1 live run showed planner quality drives outcomes. Exercised only through cassettes until funded.
- D15: (b) no live runs. Every live-model path is built, unit-tested against hand-built cassettes in the real wire format, and labelled NOT RUN LIVE in docs and on the landing page. Reason: owner constraint of zero spend. The moment the owner funds a capped run (about $12 total for all of AI_PLAN), D15 becomes (a) and nothing else changes.

Consequence for AI_PLAN: items 3A.2 smoke, 3A.4 cache check, 3A.7 batch runner, 3B.5 recording, 3B.8 red team, 3C.4 citations, 3D.2 live loop, 3E.1 recording, 3E.3 judge and 3E.4 live report are implemented and tested offline; their live numbers are reported as NOT RUN LIVE. Hand-built cassettes are produced from the mock's canonical outputs serialised in the Messages API response shape, so the real client's parsing path is still exercised in CI.

## 7. What this plan will not deliver

No alpha, no tradable strategy, no desk-ready product. After v0.4 the honest description is: a
correct, auditable, fail-closed research and TCA pipeline with a replayable real-model evaluation,
demonstrated on realistic synthetic data and one preregistered real-data test. That is a stronger
claim than anything the repository can make today, and it is the claim the docs will make.
