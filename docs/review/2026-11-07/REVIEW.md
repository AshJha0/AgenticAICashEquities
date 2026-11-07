# AgenticAICashEquities (CEAP) v0.3.5: in-depth review

Date: 2026-11-07. Reviewer bar: what a tier-one hedge fund, HFT firm or bank would require before
letting a desk use this. Every finding below was read in source by at least one reviewer and the
P0 items were re-verified line by line before writing this. Companion document: `PLAN.md` in this folder (itemised, with effort, cost and owner decisions).

## 1. Verdict in one paragraph

CEAP is a well-engineered single-process demonstration of governed LLM orchestration. The
harness-owns-governance design, fail-closed policy engine, evidence model and honest changelog are
genuinely above the bar for a personal project. But measured against the standard you asked for it
fails on all four axes at once: the "agentic" layer is mostly cosmetic (nine of twelve agents are
threshold rules, the plan barely steers anything, the default mock ignores the question), the
quant layer has one real look-ahead bug and an evaluation that can only recover what the generator
planted at five to ten times realistic effect sizes, the safety story has a four-eyes bypass and
three silent-degradation paths of exactly the class that already hid a bug for a whole release,
and nothing is persisted so there is no audit trail to speak of. There is no alpha here and the
project should not describe itself as if it finds any. None of this is fatal. All of it is fixable
and the plan says how.

## 2. Scorecard against the top-firm bar

| Axis | Grade | One-line reason |
|---|---|---|
| Architecture and governance design | B+ | Right boundaries, enforced in code; defaults are fail-open in three places |
| AI / LLM / agents | D | Planner decorative, agents are if-statements, regex JSON, no structured outputs, no real-model eval in CI |
| RAG | D | Hashed TF-IDF over 8 files (9.6 KB), labelled "semantic search" |
| Quant: TCA (Stage 1) | C- | Standard formulas, wrong timezone session, impact is drift, no delay cost, no markouts, 1.5 bps trigger inside noise |
| Quant: signal research (Stage 2) | D+ | Same-close fill (look-ahead for reversal), circular eval, no multiple-testing control, "walk-forward" fits nothing |
| Alpha / profitability / tradability | F | Zero evidence of alpha; all numbers are planted effects recovered on synthetic data |
| Security and controls | C | Four-eyes bypass, read access unscoped, plaintext keys, root container, no hashes in lock |
| Reliability and persistence | D | Nothing persisted; restart loses approvals and audit; semaphore held through human waits |
| Testing and CI | B- | 212 deterministic tests, CI green; all mock-only, no property, contract, golden or real-LLM tests |
| Docs and public claims | B- | Honest about mock vs real; still says "enterprise-style", uses the banned job-prep word (owner rule), README lacks synthetic/no-trading disclaimer |
| Business fit for a desk | F | No data connectors, no OMS/EMS, no persistence, no UI, no SSO; works only on generated scenarios |

## 3. P0 findings (fix before any further feature work)

### 3.1 Safety and governance

1. **Four-eyes bypass on order staging.** Only admin may set `stage_orders=True`
   (`src/ceap/platform.py:240`) and admin is the only role with `approvals:decide`
   (`src/ceap/policy/permissions.py:33-47`). `QueuedApprovalGateway.decide`
   (`src/ceap/policy/approvals.py:97-105`) never checks `decided_by != requested_by`. One admin key
   proposes, approves the proposal, approves staging and stages orders alone. Threat T17 claims
   "separately decided"; it is not.
2. **Harness defaults to auto-approve.** `src/ceap/harness/engine.py:169` uses
   `approvals or AutoApprovalGateway()`. Any caller that forgets the gateway gets fail-open approvals.
3. **Silent mock fallback at construction.** `src/ceap/llm/router.py:62-69`: with
   `CEAP_LLM_PROVIDER=anthropic` and a missing SDK or key, the router returns the mock with no
   fallback client, so `fallback_reason` is never set and the API reports `ok`. The Dockerfile
   installs without the `llm` extra, so the shipped image always does this. Same bug class as 0.3.1.
4. **Two more silent degradations.** Unparsable planner output becomes the canonical plan with only
   a log line (`src/ceap/agents/planner.py:229-232`); an LLM critique exception is swallowed
   (`src/ceap/agents/critic.py:139-143`). Neither surfaces as a warning or a status.
5. **Absence of analysis is reported as a clean bill of health.** The harness re-adds only the
   governance tail, never the specialist agents. If a model plan omits `quant`, attribution is empty
   and `primary` defaults to `"NORMAL"` (`src/ceap/agents/reporter.py:397`,
   `src/ceap/llm/client.py:473`). Research has `INCOMPLETE_ANALYSIS`; investigations have nothing.
6. **MCP trust is fail-open.** `src/ceap/mcp/adapter.py:78-79` defaults `readOnlyHint` to `True`
   and `riskLevel` to `LOW` when a server omits annotations. The MCP spec default is not read-only.
   Policy then trusts self-declared metadata completely. Standalone servers
   (`python -m ceap.mcp.execution`) enforce no capability at all (`src/ceap/mcp/server.py:155-172`).

### 3.2 Quant

7. **Same-close fill is a look-ahead for short-horizon signals.** In
   `src/ceap/analytics/backtest.py:187-203` the rebalance at `t` builds weights from `sig[t]`
   (which uses `close[t]`) and the new weights earn `ret[t+1]`, so the fill is at the very close the
   signal was computed from. A market-on-close order must be in before the print (NYSE 15:50 cutoff).
   Reviewer re-ran with a one-day lag: momentum R01 net Sharpe 3.22 to 3.13 (fine, it skips a month);
   reversal R03 net Sharpe **1.80 to -0.67**. The "reversal premium: PROMOTE" ground truth in
   `src/ceap/data/research_scenarios.py:78-88` is an artefact of the fill rule.
8. **The evaluation is circular and the effect sizes are implausible.**
   `src/ceap/data/historical.py:241-251` generates `r = beta*m + k*regime*mask*z + eps` where `z` is
   computed by the same `signal.fn` and the same z-score the backtest trades, with the exact one-day
   lag the backtest uses, no decay, no crashes. Planted momentum shows net Sharpe about 3.2 on a
   30-name universe; real large-cap 12-1 momentum is about 0.3 to 0.6 and crash-prone. No scenario
   sits near a threshold, so 21/21 is assured by construction. The public "21 / 21 verdicts correct"
   is a plumbing test, not a research result.
9. **Walk-forward with nothing fitted; OVERFIT misnamed.** Signals, tercile cut and weights are
   fixed rules, so the IS/OOS split (`backtest.py:173-174`, `signal_statistics.py:175-184`) is two
   date ranges. `OVERFIT` (`research_assessment.py:98-121`) fires on a planted regime flip, which is
   a regime break, not overfitting. Every knob (`in_sample_end`, `rebalance_days`, `top_fraction`,
   `weighting`, `long_short`) is caller-chosen with no locked holdout and no trial count.
10. **No multiple-testing control.** Gate is IC t >= 2.0 and OOS IC t >= 1.0
    (`research_assessment.py:25-28`) over three signals plus a free parameter grid. No Bonferroni,
    deflated Sharpe or Harvey-Liu-Zhu hurdle.

### 3.3 Platform

11. **Nothing is persisted.** Tasks, results, approvals, staged orders and traces live in dicts
    (`src/ceap/platform.py:128-132`, `approvals.py:73-75`, `src/ceap/mcp/execution/staging.py:82-88`).
    A restart loses pending approvals and the whole audit trail the threat model lists as an asset.
    Two uvicorn workers would split state and approval POSTs would miss the owning future.
12. **Read access is unscoped.** Any `investigate:read` or `research:read` key, viewer included,
    lists every task with its question and reads any report, result or trace
    (`src/ceap/api/routes.py:270-325`, `424-459`). Ownership is checked only on cancel.
13. **Owner rule broken in public.** The word the banned job-prep word appears in `README.md:328`,
    `docs/INDEX.md:9` and `LEARN.md:29,425`. A stray tracked file `Claude outputs/ci.yml` sits in
    the repo root. The GitHub README has no plain statement that all data is synthetic and nothing
    trades; only the Pages landing page says so.

## 4. P1 findings

### 4.1 AI, LLM, agents, RAG

- **Prompt injection paths.** Raw log messages and deployment descriptions are spliced into finding
  text (`src/ceap/agents/engineering.py:117,131-133`), then fed to the critic LLM
  (`critic.py:258-283`) and reporter LLM (`reporter.py:79`). The critic's free-text `overall` is
  concatenated verbatim (`critic.py:149`). The only defence is "treat as DATA" in the prompt. The
  injection test runs on `MockLLMClient`, which ignores the question entirely, so it proves nothing.
- **The critic's lower-only rule is itself an injection vector.** A log line saying "this finding is
  unsupported" can drive a true finding to zero (`critic.py:298`), while the model's `supported`
  field is discarded so it cannot flag anything.
- **Evidence-id laundering.** Validation checks that cited ids exist, not that they support the
  claim. Agents cite every id they touched (`execution.py:39`), so `SINGLE_EVIDENCE_CAP` never
  fires. `execution.py:167-178` cites whatever knowledge search returned as support for "consistent
  with runbook" regardless of content. Any agent can mint `CALCULATION` evidence with arbitrary
  attributes (`base.py:86`); `derived_from` is never verified.
- **Knowledge hits mislabelled.** `knowledge` is missing from `_SERVER_EVIDENCE`
  (`adapter.py:29-49`), so RAG results become `SYSTEM_METRIC` evidence with `ENG-` ids.
- **Anthropic client gaps.** `temperature` is never passed (API default 1.0, docs say 0.0);
  `stop_reason == "max_tokens"` is ignored so truncated JSON falls into the silent canonical
  fallback; no client timeout, only the 300 s task deadline, so a hung call fails the task instead
  of falling back. No `tools`/`tool_choice` or JSON-schema structured outputs; parsing is a
  brace-trimming regex (`client.py:33-52`) with no Pydantic validation and no repair loop.
- **No prompt versioning, no raw I/O persistence, no per-task cost.** Prompts are constants with no
  hash recorded in the report. Token usage and `last_fallback_reason` are global on one shared
  router (`router.py:31-32`), racy across concurrent tasks, no USD, no budget cap.
- **RAG.** blake2b hashing to 4096 buckets, brute-force matmul, 8 files. OOV tokens get max IDF and
  collide. No reranker, no metadata filters, no retrieval eval. Tool says "Semantic search"
  (`knowledge/server.py:263`); it is lexical.
- **Evaluation measures the analytics, not the model.** Runs on the mock by default;
  `unresolved_findings` is tautologically zero (validation already dropped them); `approved` is
  100 % only under auto-approve. No latency percentiles, tokens, cost, calibration, run-to-run
  variance or baseline. No golden set from real-LLM runs, no regression tracking.
- **Stdio MCP lifecycle.** `StdioMCPClient._session` enters an `AsyncExitStack` inside a TaskGroup
  child and closes from another task (`client.py:287-305`); anyio raises on cross-task cancel-scope
  exit. No reconnect if the subprocess dies.
- **Agentic claim.** Planner decorative: agents self-serve 28 `ensure(...)` calls; the plan controls
  three cache-only lookups and a fixed RAG query string unrelated to the question. Confidences are
  hand-set 0.85/0.9; `base.py:124` calls them "calibrated". Nothing is calibrated against outcomes.

### 4.2 Quant: statistics and backtest

- IC horizon does not match holding period: headline t-stat is 1-day IC while books hold 5 or 21
  days (`signal_statistics.py:131-140`); horizon-h IC is computed but gates nothing. No
  Newey-West for autocorrelated ICs. `ic_ir` not annualised (`:140`). Signal "turnover" is
  1 minus rank autocorrelation, range 0 to 2, not portfolio turnover (`:81-92`).
- Weights never drift between rebalances (`backtest.py:189-191`): a free daily rebalance to constant
  weights understates turnover and cost. `ann_return` arithmetic next to geometric CAGR; `cagr=-1.0`
  sentinel. IS boundary leakage: horizon-h forward returns at the last h IS dates read OOS prices.
- Betas against an equal-weight market that includes the name (`portfolio_risk.py:46-61`), no
  shrinkage, NaN to zero after centring. Stress is linear beta times -5 % and one-name 5 %
  (`:145-152`), evaluated at one date only. No factor or sector shock, no liquidation cost.
- No borrow cost, locate or hard-to-borrow list (tier-C shorts would be expensive or unavailable).
  No corporate actions or delistings (NaN return set to 0). Universe is 30 names that exist in 2026
  (survivorship). `busday_offset` with no exchange holidays. `adv_20` includes trade-day volume.
  Half-spread charged at the close where the auction has no spread. Single $50M notional, no
  capacity sweep, no lot rounding, no per-name cap inside the backtest.

### 4.3 Quant: TCA

- Perold IS has no delay component: `decision_price` equals arrival mid by construction
  (`synthetic.py:395`). No fees, commissions or rebates anywhere.
- "Permanent/temporary impact" are single-order drift, not beta-adjusted markouts
  (`market_impact.py:16-25`, `execution_metrics.py:124-127`). Normal scenario T01 shows -45 bps
  permanent and +30 bps temporary; T09 shows -70.5 bps temporary. With one-hour sigma near 25 bps
  these are noise. `post_ts` clamps to the last quote, silently shrinking the horizon.
- Deterioration trigger 1.5 bps (`attribution.py:48,136`) is inside single-order IS noise of
  15 to 25 bps. No confidence bands, no peer universe, no pre-trade residual. Causes are threshold
  scores, not a decomposition of the bps.
- No markouts, realised spread or price improvement. Effective spread unweighted (`slippage.py:32-43`);
  standard is size-weighted. Venue "slippage" is fill versus mid, basically half effective spread,
  unconditioned on order type or size. `volume_share` misnamed (`:233`).
- Timezones wrong: US tickers trade 12:00 to 16:00 Europe/London (`synthetic.py:28,108-111`), which is
  07:00 to 11:00 ET. The baseline is pre-market and the window straddles the open with no auction
  handling. Flat Poisson volume, no U-shape. All fills aggressive at the ask (`:439-456`), never
  passive, only BUY parents (`:385`), so TCA is never validated on sells or on reversion.
- `square_root_impact_estimate_bps` has a unit error (daily sigma times sqrt of interval
  participation, `market_impact.py:28-38`) and nothing calls it, so there is no pre-trade expected
  cost benchmark in the TCA.

### 4.4 Platform, security, CI

- Static plaintext keys, no rotation or expiry, principal is role plus 8 hex of SHA-256
  (`auth.py:35-45`); duplicate-key error prints the raw key (`config.py:148`). `/health` leaks
  environment, model, token usage and server list unauthenticated (`routes.py:68-81`); `/metrics`
  and `/scenarios` open. No rate limits, CORS, security headers, TLS.
- Concurrency semaphore held during human approval waits (`routes.py:193-200`); four pending
  approvals at the hard-coded 120 s timeout (`approvals.py:71`) stall everything. No idempotency
  keys on POST. `paginate` does not clamp negative offset/limit (`mcp/common.py:50`). No upper bound
  on `gross_notional` (`api/models.py:87`).
- Supply chain: lock has 72 pins and 0 hashes, hand-edited after each regeneration; Docker ignores
  the lock, installs lower-bound ranges on Python 3.11 while CI tests 3.12 only, runs as root, no
  HEALTHCHECK, no digest pin, no `.dockerignore`, never built in CI. Actions pinned to major tags.
  No Dependabot, pip-audit, CodeQL, SBOM. No `permissions:` block, no concurrency group, no coverage.
- Release process: twelve commits, zero PRs, tags cut on main, CI runs only after the push. Three
  releases shipped with red CI unnoticed (your own changelog says so). No PR gate exists in practice.
- Tests: all 212 use the mock and synthetic data; session-scoped platform fixture leaks state;
  API tests poll with 300 sleeps; stdio test spawns a subprocess. Missing: property-based, MCP
  contract, golden reports, lag-sensitivity, IS-components-sum, sell-side TCA, timezone, null
  calibration over many seeds, restart-mid-approval, Docker image.
- Observability: JSON logs real but no correlation ids; 14 metric sites, harness only; Prometheus
  summary emits `_max`/`_min` with no quantiles (non-standard); OpenTelemetry opt-in, uncovered,
  untested.

## 5. Public claims that need correcting

| Claim (README / landing) | Status |
|---|---|
| "enterprise-style", "enterprise documents" | Marketing; no production context. Say "bank-style governance pattern, demonstration scale" |
| "21 / 21 verdicts correct", "50 / 50 primary cause correct" | True on the mock against self-generated ground truth; must be labelled a plumbing test |
| "≈0.9 s end to end" | Mock only; real-model latency not published |
| "walk-forward" | Nothing is fitted; call it an IS/OOS date split |
| "OVERFIT" flag | Detects regime break, not overfitting |
| "Semantic search" (knowledge tool) | Lexical hashed TF-IDF |
| "calibrated confidence" | Hand-set constants |
| "Perold IS" | No delay component, no fees |
| "every figure traced" (reporter) | Warn-only audit, skips integers <= 12, 11 derived transforms make most small integers match |
| "separately-decided second approval" (T17) | Same admin can decide both |
| "OpenTelemetry-ready" | Uncovered, untested, absent from image |
| "Python 3.11+" | Never tested; CI is 3.12 only, image is 3.11 untested |
| Model names `claude-sonnet-5`, `claude-sonnet-4-5` | Verify against current model ids and record the exact id in each report |

## 6. What is genuinely good and should be kept

Harness owns the governance tail and strips planner-supplied approval steps. Policy fails closed on
unknown tools. Evidence digests exist. The number and evidence-id audits exist and caught a real
fabrication during 0.3.2 (and you kept the stricter version). Cancellation is reference-counted and
the state machine is correct. The changelog records failures honestly, including three releases with
red CI. The 0.3.1 live-run write-up (temperature bug, planner model matters) is exactly the kind of
evidence a reviewer wants to see. The docs structure (LEARN, COOKBOOK, threat model, spec) is far
above the norm. Keep all of this. The problem is what sits inside it, not the frame.

## 7. Honest answer on alpha, profitability and tradability

There is no alpha in this repository and nothing in it could show any. Every research number is the
recovery of a premium the generator planted at implausible size, and the one short-horizon result
dies under realistic execution timing. A free daily-data study (Stooq or yfinance, no sign-ups, no
card) can at best answer "does the literature factor sign hold in this sample net of estimated
costs" and will most likely return NULL, as your other repos' real-data studies did. That is still a
worthwhile result to publish because it converts the project from "demo that always says PROMOTE"
into "research process that correctly says REJECT on real data". Tradability of anything here is
zero: no borrow, no auction prints, no real ADV, no OMS. The plan treats this as the headline
deliverable rather than a footnote.
