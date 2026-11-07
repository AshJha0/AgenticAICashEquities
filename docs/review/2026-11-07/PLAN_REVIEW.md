# Review of plan v1 (what was wrong, and what v2 changed)

Date: 2026-11-07. Three independent reviewers (quant and execution research, AI platform, delivery
engineering) read `PLAN_v1_superseded.md` and `AI_PLAN_v1_superseded.md` against the source. Their
findings were verified in code where they made a factual claim, then folded into `PLAN.md` v2. This
file is the record so a later session understands why v2 looks the way it does.

## 1. Findings that changed the plan

### Contradictions and impossible acceptance tests
- Ten acceptance tests in the AI plan required live model calls (cache-read tokens, batched cost,
  injection pass rate on a real model, Haiku red team, recorded cassettes, judge agreement, live
  latency report) while D15 said no live calls. **v2:** every acceptance test is offline; live
  metrics are printed as UNKNOWN / NOT RUN LIVE (3E.2).
- "Trained ML models" was a non-goal while round 2B added eight models. **v2:** non-goal is "ML trained
  on real data"; synthetic-fitted models are labelled with the generator hash.
- The cassettes were to be built from the mock, so "mock parity" compared the mock with itself.
  **v2:** cassettes are authored from the golden set in the full wire format with provenance tags;
  the differential test is renamed "adapter equivalence" (3A.5, 3A.8).
- The Files API item claimed token savings it cannot deliver (only caching does). **Cut.**
- `count_tokens` is a network call, so it cannot back a zero-spend budget cap. **v2:** local estimate (3A.6).
- Item 1.9 duplicated 3A.1, 3A.3 and 3A.8. **Merged** into 1.9.

### Quant errors
- The hurdle t >= 3 was chosen before the null sweep and would make PROMOTE unreachable: with 504
  in-sample days and realistic Sharpe 0.3 to 0.8, t is about 0.4 to 1.1. **v2:** 20-year synthetic
  history (2.9), hurdle derived from the null sweep with a 5 % upper bound (2.10), ledger-counted
  trials (2.8); the single preregistered hypothesis uses alpha 0.05 (D4 revised).
- Ground truth "matches a fresh run" was circular. **v2:** ground truth from generator parameters;
  power curve reported (2.9, 2.11).
- An iid bootstrap over fills misses the shared drift that dominates single-order IS noise. **v2:**
  analytic sigma*sqrt(T/3) plus peer dispersion (2T.4).
- U-shaped volume with an immediately-preceding baseline confounds attribution. **v2:**
  time-of-day-matched baseline over previous sessions, which needs the multi-session generator (2T.1, 2T.2).
- Beta adjustment and peer matching were specified against an index and peers that do not exist in
  the one-day, two-order synthetic data. **v2:** synthetic index and 20 sessions x 40 parents (2T.1).
- The regime HMM could not pass its own test because R04 changes only the premium. **Cut.**
- A GBM cost model trained on generator fills learns the generator. **v2:** parametric fitted
  square-root model with shift-tested conformal coverage (2T.8).
- Delisting default of -100 % was wrong. **v2:** two bounds, merger and performance (2.5).
- The claimed sign of survivorship bias for long-short momentum was unjustified. **v2:** measured with
  a survivor-only parallel run (5.2).
- Round 5 had no power analysis: a 2.8-year holdout can detect Sharpe about 1.5, and "Sharpe > 0" passes
  half the time under the null. **v2:** 2005 to 2023 evaluation, minimum detectable Sharpe 0.57,
  UMD replication gate, allowed and forbidden claims (5.1, 5.5, 5.6).
- The engine selects one parent per dataset, so broker, algo and venue comparison was impossible.
  **v2:** 2T.1 and 2T.7.
- Missing conventions a desk would ask about first: excess returns, short rebate, cash drag, gross
  leverage, Reg SHO SSR, half days, special closures, dated ticker maps, split checks. **v2:** 2.3, 2.4,
  5.2, 5.3.

### Agentic-AI design errors
- "Critic re-calls the same tool" cannot overturn anything, and letting `supported=False` lower
  confidence turns the known injection vector into a veto. **v2:** disputes count only through a
  different deterministic check that contradicts the value at the evidence path (3B.3).
- "Critic scores hypotheses" let the model judge evidence. **v2:** hypothesis types map to
  pre-registered deterministic tests; the model proposes and ranks (3B.2).
- Planner choosing the baseline length handed the model researcher degrees of freedom and conflicted
  with window validation. **v2:** enumerated set, each choice is a ledger trial (3B.1).
- Calibration fitted and scored on the same planted scenarios. **v2:** held-out seeds, shipped only
  if it helps (2B.4).
- Case memory would contaminate the evaluation. **v2:** off in eval, partitioned by dataset (3B.7).
- The sandboxed prototyping agent: an import allowlist is not a sandbox, no seccomp on Windows,
  unexercisable offline, and the largest multiple-testing engine in the project. **Cut; D13 reversed.**
- Mid-conversation system messages on single-turn calls were cargo cult. **Cut.**
- Thinking tokens count against `max_tokens`; current limits (4096/2048/3000) would truncate at effort
  high and turn every run DEGRADED. **v2:** headroom derived from effort (1.9); cassettes carry
  `thinking` and `redacted_thinking` blocks (3A.5).
- Strict schemas need `additionalProperties: false`, so the planner's free-form `arguments` must become
  a discriminated union generated from the MCP tool schemas (3A.1).
- Citations and structured outputs are mutually exclusive in one call. **v2:** two-call reporter design
  (3C.4).
- Missing entirely: typed agent messages, re-planning, context budget, replay bundle, kill switch,
  model and SDK pin file, degradation ladder, per-surface injection threat model, PII redaction,
  retention. **v2:** 3B.6, 4.7, 1.5, 1.9, 3B.5, 0.8.

### Delivery errors
- "Rounds 2, 3, 4 touch disjoint files" was false; `engine.py` alone was touched by nine items.
  **v2:** contracts PR first (0.7), conflict map (section 5), serial R3A → R3B → R3E, two sessions max (D17).
- MCP `riskLevel: LOW` defaults exist in `client.py:133` and `client.py:168` as well as the adapter.
  **v2:** 1.7 names all three.
- No dev environment exists: `pytest --co` fails with five collection errors on this machine.
  **v2:** bootstrap script (0.1).
- `core.autocrlf=true` with no `.gitattributes` would make prompt hashes and the audit chain differ
  between Windows and Linux. **v2:** 0.3.
- The pip-tools lock cannot be regenerated on Windows and is not marker-aware for a 3.11/3.12 matrix.
  **v2:** `uv pip compile --universal --generate-hashes` in CI (0.2).
- Session-scoped fixtures leak state and block parallel tests; D1 and the deny-default break every
  test that approves with the admin key. **v2:** fixture refactor once in R0 (0.6).
- Persistence written while result shapes were still changing, with no migration mechanism. **v2:**
  contracts PR, `user_version`, `schema_version` (0.7, 4.1).
- Restart semantics were hand-waved ("approval can be decided"): the awaiting coroutine is gone.
  **v2:** INTERRUPTED and EXPIRED, 409 on late decisions, conditional update (4.2).
- CI hardening sat in Round 4 but gated everything. **v2:** R0.
- Fifteen breaking changes in one 0.4.0. **v2:** release train (D16).
- Estimates ignored the docs pass per round (owner rule), the rebase tax across eight branches, and
  CI cycle time growth. **v2:** 701 h vs 420 h; 6 to 7 calendar weeks vs 2 to 3.
- `xgboost` listed and unused. **Removed in 0.2.**
- Hand-cut tags shipped red three times. **v2:** tag-triggered `release.yml` that refuses red SHAs (0.4).

## 2. Findings considered and not adopted
- Dropping Python 3.11 support to avoid the matrix: kept, because `uv --universal` makes the matrix
  cheap and the README promises 3.11+.
- Using the Anthropic memory tool for case memory: not needed, the harness injects cases itself.
- Local dense embeddings now: deferred to post-1.0; torch in the lock is disproportionate.
- 1.0.0-rc after 0.8.0: not scheduled; only when API and database schema are frozen.

## 3. Verification performed by the coordinating session
Confirmed in source before accepting: `mcp/client.py:133` and `:168` defaults; session-scoped fixtures
in `tests/conftest.py`; `core.autocrlf=true` and no `.gitattributes`; `xgboost` unused; single-parent
selection in `execution_metrics.py:66-72`; `max_tokens` 4096/2048/3000 in planner, critic, reporter;
historical dataset split constants in `data/historical.py`. Estimates from the three reviewers were
reconciled by taking the higher figure where they disagreed and adding a 10 % conflict tax.
