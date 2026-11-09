# CEAP review and plan, 2026-11-09 (with 0.8.0 status)

## Status after 0.8.0

This is the two-round review and plan written at v0.7.1, copied into the repository unchanged below
the line. The status here is taken from the `## Unreleased` section of `CHANGELOG.md` (0.8.0). The
plan's release train (section 4) put research realism in 0.10.0 and execution in 0.11.0; seven
parallel lanes pulled most of both into 0.8.0, so the train below the line is out of date. Code
comments that say "0.10" refer to that old numbering and mean this release.

| Workstream | Done in 0.8.0 | Partial | Moves to 0.9.0 |
|---|---|---|---|
| WS-A guardrails | A1-A20 | – | – |
| WS-B AI evaluation | B2, B3, B6, B9 | – | B1, B4, B5, B7, B8 |
| WS-C retrieval | C1, C3 | – | C2 |
| WS-D research | D1-D4, D6-D9, D11-D23 | – | D5 (500-name universe), D10 (delisting returns) |
| WS-E real data | – | – | E1, E2 |
| WS-F execution | F1-F4, F6-F9, F11, F14 | F5 (auction model done; paper MOC caps not), F12 (tick, round lot, LULD done; sigma fat-finger band and halts not) | F10, F13, F5 paper half, F12 rest, the paper-execution parts of F11 |
| WS-G platform | G1, G3, G6, G7 | G2 (about 5x on `_trades`, 10x on `generate`, measured on the dev machine; target was 50x) | G4, G5, G8-G13 |
| WS-H engineering | H5, H8, H9 | H2 (branch coverage on, floor 85 % not 90 %; no per-package 95 %) | H1, H3, H4, H6, H7, H10-H15 |
| WS-I docs | I3, I4 (this pass) | – | I1, I2, I5 |

Notes:
- F11: lane 3 delivered the three parts in the plan (staged sets persisted in SQLite, pre-trade
  check before staging, DB-backed kill switch checked by staging and the paper job). The execution
  lane recorded "F11" among its deferrals; that refers to wiring the new `ceap.execution` simulator
  into paper trading, which is 0.9.0.
- The new research golden items (`tests/golden/golden_set_research_v1_1.json`) and the
  out-of-distribution split (`tests/golden/golden_ood_v1.json`) await owner sign-off.
- Owner decisions D1-D4 were approved on 2026-11-09. D4 (breaking changes) is listed under
  "Breaking changes in 0.8.0" in `CHANGELOG.md`.

---

# CEAP (AgenticAICashEquities) — two-round review and improvement plan

Date 2026-11-09. Reviewed at v0.7.1 (`ebc9dfb`). Zero spend, no card details, no paid data.

**Reviews merged.** Round 1 was AI / agents / LLM / RAG / guardrails (R1-AI), ML and quant research
(R1-Q), quant development, execution and TCA (R1-X), and tests / code / CI / docs (R1-E). Round 2
ran independently with new lenses: model risk and red team (R2-MR), research committee (R2-RC),
platform reliability and algos (R2-PR), and learning and test design (R2-DT, **pending; added when it
reports**). Every finding is a step below. Where two reviews found the same issue, it is one step
carrying both tags. Findings the coordinator checked and found wrong are listed at the end, not
silently dropped.

**Checked by the coordinator before writing this plan** (re-run, not taken on trust):
- The number audit passes a sign flip ("-12.34 bps") and 100x errors ("1234 bps", "43 pct").
- Flagged narrative still ships (warning only).
- All 10 cassettes are `hand_built`.
- The policy engine checks only an argument named `symbol`.
- The audit hash is plain unkeyed SHA-256.
- `stage_orders` does not call `check_orders`.
- Passive fills earn the touch with no queue.
- Impact is a planted drift.
- The coverage gate is 80 %.
- There is no Dependabot.
- `ResearchVerdict` has only PROMOTE and REJECT.
- The IC hurdle is t >= 0.4.
- The budget exists but is off by default.

## 1. Verdict (both rounds agree)

A well-governed **research harness**, not yet a validated AI system, research result or trading
system.

- **Strong:** planner containment, data-block isolation, honest provenance labels, a correct Perold IS
  decomposition, a correct deflated Sharpe, preregistration discipline, a hashed lock, SHA-pinned
  actions and a non-root image.
- **Weak, with the evidence:**
  1. No AI metric depends on a model. Every number comes from deterministic code or hand-built
     cassettes.
  2. Two guardrails can be beaten in one line: the number audit and the symbol policy.
  3. The audit log can be rewritten and re-chained.
  4. The synthetic research premia are 2-3x real, so the verdicts are circular.
  5. There is no execution algorithm, and the simulator gives free passive fills.
  6. CPU-bound work blocks the event loop, so the timeouts are fiction.
  7. There is no real-data result.

## 2. Workstreams and steps

Effort is in engineering hours. Tags show the source reviews.

### WS-A Guardrails and AI safety (correctness first)
| # | Step | Tags | h |
|---|---|---|---|
| A1 | Replace the free-text number audit with typed placeholders. The model writes `{fact.key}` and code renders the numbers, with units. Until that lands: drop `-v` unless the direction word agrees, allow only unit-matched conversions, and remove the integer <=12 exemption. | R1-AI C1, R2-MR H1 | 10 |
| A2 | When the narrative fails the number or ID audit, replace the prose with the template narrative. Keep the rejected prose only in the audit record. Update the adversarial tests to assert suppression. | R1-AI C2 | 2 |
| A3 | Policy engine: validate arguments against each tool's JSON schema. Tools declare their symbol-bearing fields (lists and nested too) and every one is checked. Recursive length guard. Numeric coercion before `limit`. Fail closed when no universe is set. | R1-AI H2, R2-MR C2 | 9 |
| A4 | HIGH-risk tools always need approval; the capability decides only who may approve. Admins go through four-eyes. | R1-AI H2, R2-MR C2 | 2 |
| A5 | Untrusted MCP servers are denied by default, even when they self-declare read-only. Hash the tool catalogue at start-up and refuse on drift. | R1-AI H2, R2-MR L2 | 4 |
| A6 | Audit log and paper ledger: HMAC-keyed chain (key outside the DB). `verify_chain(expected_head, rows)`. Anchor the chain head externally: git commit or tag, plus a free OpenTimestamps proof. | R2-MR C1, R2-PR H3 | 8 |
| A7 | Per-claim citation mapping: a policy claim without its own verified citation span is unsupported. | R2-MR H2 | 4 |
| A8 | Data-block hardening: NFKC normalisation, strip zero-width characters, whitespace-tolerant regex, a random nonce per block tag. | R2-MR M1 | 2 |
| A9 | Redaction: `key=value` secrets, API-key env names, phone numbers, high-entropy tokens. | R2-MR M2 | 2 |
| A10 | Budget on by default and race-free. Reserve before the call, settle after. Cap per task on tokens, USD and number of calls. Circuit breaker after N fallbacks. Per-principal rate limit. | R1-AI H3, R2-MR M3, R2-PR H4 | 6 |
| A11 | Planner sanitiser: allowlist string-argument values; drop a step when its scope is unknown (list symbol with no universe). | R2-MR M4 | 3 |
| A12 | Repair loop: echo only the structured error list, never raw model output, or wrap it in a data block. | R1-AI M4 | 1 |
| A13 | Four-eyes on the person, not the key: map keys to a named subject. | R2-MR M5 | 4 |
| A14 | The approval view shows the exact tool arguments and content hash; re-check the hash just before execution. | R2-MR M6 | 4 |
| A15 | Sign or verify prompt hashes and model pins against `model_pins.toml` at load. | R1-AI miss-7, R2-MR L3 | 2 |
| A16 | Per-agent least privilege: each agent gets its own capability set and tool allowlist. | R2-MR miss-10 | 5 |
| A17 | Knowledge ingestion screens documents for injected instructions; filter `effective_date <= as_of` at query time; reject superseded versions. | R1-AI H4, R2-MR miss-6 | 4 |
| A18 | Threat model: an OWASP LLM Top-10 table (LLM01-LLM10), each control mapped to code and tests. | R1-AI H1 | 2 |
| A19 | Cassette matching: exact by default; prefix only for cache tests; fail on unused interactions. | R1-AI M5 | 2 |
| A20 | Label or zero the mock's `len//4` token and cost figures. | R1-AI L1 | 0.5 |

### WS-B Real AI evaluation (turn "NOT RUN LIVE" into measurements, still zero cost)
| # | Step | Tags | h |
|---|---|---|---|
| B1 | `OllamaLLMClient` (or llama.cpp) behind `LLMClient`, using a local open-weight model (Qwen2.5-7B/14B-Instruct or Llama-3.1-8B) with grammar or JSON-schema output. Cassettes carry `provenance: local_model`. Run golden, adversarial and claims at n>=3 seeds. **Needs a free model download, about 5-9 GB (owner decision D1).** | R1-AI C3/miss-1, R2-MR miss-2 | 12 |
| B2 | Per-agent evals. Planner: tool-selection precision and recall against a gold plan. Critic: detection rate on planted contradictions. Reporter: faithfulness rate on injected claims. Also plan-valid %, repair %, claim-drop % and citation-verified %. | R2-MR C3, R1-AI C3 | 20 |
| B3 | Metric sensitivity test: swap in deliberately degraded mocks or prompts and assert each AI metric moves (proves the metrics can detect a bad model). | R2-MR C3 | 4 |
| B4 | Injection and jailbreak corpus (about 200 attacks, written locally) run against the local model nightly, reporting obey rate per surface. | R1-AI H1/miss-4 | 10 |
| B5 | Local LLM-as-judge: implement the pinned-but-unused `judge` purpose. Rubric of faithfulness, completeness and hedging. Calibrate against 50 human labels and publish only when Cohen's κ > 0.6. | R1-AI M2/miss-3 | 8 |
| B6 | Golden set out-of-distribution split: freeze 2-3 templates the rule authors never see; per-template accuracy with CIs. | R1-AI M1 | 6 |
| B7 | A local challenger LLM alongside the main one: plan agreement shown to the approver. | R2-MR miss-2 | 4 |
| B8 | Runtime AI SLOs: plan-valid, repair, fallback, untraceable-number rate and p95 latency per purpose, as OTel GenAI spans and metrics, with rolling SPC alerts (drift in operation, not only config). | R1-AI M3/miss-6, R2-MR miss-4/9 | 6 |
| B9 | Docs: state plainly that only the planner, critic and reporter use the LLM, and the specialists are deterministic modules. | R1-AI L2 | 1 |

### WS-C Retrieval (RAG)
| # | Step | Tags | h |
|---|---|---|---|
| C1 | Expand the retrieval eval to 150+ queries (paraphrases, typos, multi-hop, near-miss negatives) with bootstrap or Wilson CIs. Today's n=10 held-out R@3 = 1.00 has a lower bound of about 0.72. | R1-AI H4 | 10 |
| C2 | Hybrid retrieval: local sentence-transformers (bge-small or all-MiniLM, CPU), numpy cosine, RRF with BM25, and a local cross-encoder reranker, all compared against BM25 on C1. **Free model download (D1).** | R1-AI miss-2, R2-MR miss-3 | 6 |
| C3 | Corpus governance: version pinning and staleness check (pairs with A17); keep release notes out of corpus files (lesson from 0.7.0). | R1-AI H4 | 2 |

### WS-D ML and quant research
| # | Step | Tags | h |
|---|---|---|---|
| D1 | Recalibrate the synthetic premia to real moments: monthly IC about 0.02-0.03, gross long-short Sharpe about 0.3-0.5, cross-sectional expected-return SD about 3-6 %/yr (today 24 %/yr). Re-label R08 and R09 ("realistic" and "weak" are not). | R2-RC C2, R1-Q C1 | 6 |
| D2 | Adversarial scenarios written blind to the detector: alpha in a hidden factor, regime switching, GARCH plus jumps, signal correlated with the market. Report verdict accuracy as a power curve against the planted premium, not a pass count. | R1-Q C1, R2-RC C1 | 16 |
| D3 | Verdict rule: an a-priori IC hurdle (t >= 1.645 for a single preregistered test, >= 3 for mined ones). FPR **and** power curves across several generator families. | R2-RC C1 | 8 |
| D4 | Add an **INCONCLUSIVE** verdict (CI covers both 0 and the minimum relevant Sharpe); MONITOR if justified. Fix the test and docs that already claim four verdicts. | R2-RC H3 (+coordinator) | 3 |
| D5 | Universe of 500 synthetic names (deciles of 3 names are meaningless). | R2-RC H1 | 4 |
| D6 | Robustness report per verdict: halves, sub-periods, vol regimes, lookback and skip neighbourhood, cost x0.5/1/2/3. | R2-RC H2 | 8 |
| D7 | End-to-end leakage canary: a feature equal to the realised t+1 return, delayed 0..k days, through `simulate_backtest`; assert Sharpe collapses exactly when delay >= execution lag. Lower `t_max`. | R1-Q H1 | 6 |
| D8 | Deflated Sharpe trial accounting: floor `n_trials` at the preregistered variants; effective N by clustering correlated trials; variance on the same horizon as T. | R1-Q H2 | 6 |
| D9 | PBO/CSCV on the trial matrix (S=16) and a stationary block bootstrap CI for OOS Sharpe and IC. | R1-Q H4 | 8 |
| D10 | Real-path delisting returns (Shumway-style by exit type) and keep names with a missing close; sensitivity band. | R1-Q H3 | 3 |
| D11 | Newey-West: masked autocovariances over gaps; Andrews automatic bandwidth for persistent signals. | R1-Q M1, R2-RC M3 | 2 |
| D12 | ML baseline: an equal-weight z-score composite as the explicit baseline, with a paired block-bootstrap or Diebold-Mariano difference test; coefficient paths across folds. | R2-RC H4, R1-Q M2 (stability part) | 5 |
| D13 | Risk model: Ledoit-Wolf covariance, FF5+UMD regression of book returns, multi-factor stress scenarios. | R1-Q M3 | 6 |
| D14 | Data vintage: hash each downloaded series and diff on re-download (vendor re-adjustment). | R1-Q M4 | 3 |
| D15 | Alpha decay: an IC term structure h=1..60 with a fitted half-life, checked against the generator truth. | R1-Q miss-3, R2-RC miss-2 | 3 |
| D16 | Factor-neutral research: residualise on FF5+UMD and industry; alpha t after factors. | R1-Q miss-2, R2-RC miss-1 | 6 |
| D17 | Capacity curve (net Sharpe against AUM via the existing impact); turnover against net-Sharpe frontier. | R1-Q miss-5, R2-RC miss-5/6 | 5 |
| D18 | Cost-aware construction: turnover-penalised mean-variance (closed form or projected gradient) against rank weights. | R1-Q miss-6, R2-RC miss-4 | 8 |
| D19 | Drawdown, CVaR, skew and crash analysis (R10); regime conditioning (GARCH state, VIX from FRED). | R2-RC miss-3/8, R1-Q miss-7 | 5 |
| D20 | Reproducibility: a uniform per-study rebuild script (prereg plus data hashes); publish the null-sweep per-seed vector and a 50-seed quick check. | R1-Q miss-8, R2-RC M1 | 5 |
| D21 | Freeze the generator version per sweep and record re-sweeps in the ledger (forking-paths control). | R2-RC M2 | 2 |
| D22 | Model inventory: extend `ModelCard` (owner, validator, date, status, tier, review cadence); register the pre-trade cost model and each LLM purpose; fix the approval text "no pre-trade cost model". Add a naive challenger to each card. | R2-MR H3/L1 | 8 |
| D23 | Label `ic_ir_annualised` descriptive in outputs; document the flat borrow assumption. | R1-Q L1/L3 | 1 |

### WS-E Real-data study (the one that can actually pass on free data)
| # | Step | Tags | h |
|---|---|---|---|
| E1 | Preregister and run a **portfolio-level** study on survivorship-free free data. Industry (or factor) momentum on the 49 Ken French industries, long top 7 / short bottom 7, market-state conditioning stated in advance. In-sample 1927-1989; holdout 1990-2015 opened once; live 2016+. One-sided Newey-West t (Andrews), critical value 1.645, FF5+UMD alpha t secondary, block-bootstrap Sharpe CI, MDE Sharpe about 0.49. Costs 10-20 bps/side with breakeven. Gates: replicate the published in-sample Sharpe ±0.15 and ETF-sector correlation >= 0.8 from 1999. Allowed claims are written in advance. **Free downloads: Ken French, FRED, AQR (owner decision D2).** | R2-RC §3, R1-Q miss-1 | 30 |
| E2 | Keep the stock-level R5 closed; document why it could not pass (predictable from vendor coverage) as a process lesson. | R2-RC C3, R1-Q C2 | 1 |

### WS-F Execution, microstructure and algos
| # | Step | Tags | h |
|---|---|---|---|
| F1 | Event-driven LOB simulator: price-time-priority book, FIFO queue position from displayed size, event or ms clock, order-entry and market-data latency, hidden liquidity. Passive fills only when traded through, with adverse selection. | R1-X C1/H3/miss-1, R2-PR miss-1 | 40 |
| F2 | Endogenous impact: a propagator or Obizhaeva-Wang kernel driven by the bot's own signed flow; fit permanent and temporary impact from multi-horizon markouts and report against truth. | R1-X C2 | 10 |
| F3 | Schedulers: TWAP, VWAP (U-profile), POV with band, IS via Almgren-Chriss (closed-form sinh trajectory, λ frontier); one `Schedule` interface. | R1-X H1/miss-2, R2-PR miss-1 | 16 |
| F4 | Child-order logic: passive/aggressive by spread and urgency, I-would limits, cancel/replace, min qty, randomisation. | R2-PR miss-1 | 16 |
| F5 | Open/close auction model (imbalance, indicative price, auction share of ADV). Paper MOC fills capped at an auction fraction with auction impact; align the pre-trade 25 % and fill 10 % limits. | R1-X H2/miss-4, R2-PR miss-1 | 12 |
| F6 | Predicted vs realised cost loop: pre-trade AC estimate, post-trade Perold, residuals in SQLite, weekly recalibration of Y and exponent with drift alerts, reliability plot. | R1-X miss-5, R2-PR miss-1 | 12 |
| F7 | Markout curves (1s..close) by liquidity flag and venue with bootstrap CIs; a venue/SOR routing table instead of a cause score. | R1-X miss-3/6, R2-PR miss-1 | 12 |
| F8 | Cause attribution: a cross-sectional regression of the IS residual on standardised regressors, with each cause's bps share and CIs, replacing fixed ratio thresholds. | R1-X M1 | 10 |
| F9 | IS partial fills without a final price: return `fill_rate` and flag or NaN the opportunity cost. | R1-X M2 | 1 |
| F10 | Money as integer micro-dollars or Decimal strings in ledger bodies; explicit lot rounding with a residual event. | R1-X M3 | 5 |
| F11 | Staging path: persist staged sets in SQLite (unique `staging_id`); call `check_orders` before staging; DB-backed kill switch checked by both staging and the job, set/clear audited. | R1-X H4/M4, R2-PR miss-3 | 7 |
| F12 | Fat-finger band tied to σ (about 6σ daily, 10 % cap); LULD bands, tick rules, odd/round lots, halts. | R1-X M5/miss-7 | 7 |
| F13 | Dividends as cash events in the ledger (total return, short dividend cost). | R2-PR M4 | 6 |
| F14 | Cleanups: `london()` alias, `vwap_by_bucket` via bincount, deprecate noisy market-impact helpers. | R1-X L1-L3 | 1.5 |

### WS-G Platform reliability and performance
| # | Step | Tags | h |
|---|---|---|---|
| G1 | Get CPU work off the event loop: `validate_request` and generation via `asyncio.to_thread`; per-key locks; pre-generate or cache datasets to disk. **Today a cold dataset blocks the server for about 77 s and fails the health check.** | R2-PR C1 | 4 |
| G2 | Vectorise `synthetic._trades` (numpy columns, lazy objects); target over 50x. | R2-PR C1 | 6 |
| G3 | CPU tools in a killable `ProcessPoolExecutor` (metadata `cpu_bound`) so step and task timeouts and cancellation actually fire. | R2-PR C2 | 8 |
| G4 | Backtest hot loop: NaN-fill once, cumulative-product holds, capacity sweep reuses weights; target 5-10x. Goldens pin outputs. | R2-PR H1 | 10 |
| G5 | Durable runs: persist state at step boundaries; resume after a crash; resumable approvals; idempotency key per ToolRequest. | R2-MR H4 | 18 |
| G6 | Paper dead-man's switch: on failure open a GitHub issue; weekly staleness check of the `forward-log` head against the NYSE calendar; last-session badge; protect `forward-log` from force-push. | R2-PR H2/H3 | 4 |
| G7 | SQLite durability: `synchronous=FULL` for audit and paper; timeout on `arun`; `/health` reports writer queue and a write probe. | R2-PR M1 | 3 |
| G8 | Periodic TTL sweep of pending approvals in `lifespan`. | R2-PR M2 | 2 |
| G9 | Operations: `docs/RUNBOOK.md` (stuck task, chain break, disk full, restart), Prometheus alert rules, event-loop lag gauge. | R2-PR M3 | 5 |
| G10 | Chaos and property tests: kill -9 mid-`run_day` converges; injected `disk I/O error`; event-loop lag test. | R2-PR miss-2 | 6 |
| G11 | Paper-day replay gate: recompute yesterday from the raw feed and diff. Forward-log status page on Pages. | R2-PR miss-4/5 | 7 |
| G12 | Calendar: fail loudly after 2030; manual dispatch uses the NY date, not UTC. | R2-PR low | 1 |
| G13 | Docker multi-stage (no pip in runtime); a reverse-proxy compose example. | R2-PR low/H4 | 2 |

### WS-H Engineering, CI and supply chain
| # | Step | Tags | h |
|---|---|---|---|
| H1 | mypy strict for `domain`, `analytics` and `api` (per-module), no global `ignore_missing_imports`. | R1-E H1 | 8 |
| H2 | Coverage: `--cov-branch --cov-fail-under=90` plus 95 % on analytics and domain. | R1-E H2 | 1 |
| H3 | Close the mutation gaps (non-positive mids, NaN final price) and widen Hypothesis strategies; mutmut nightly on analytics and domain with a surviving-mutant ratchet. | R1-E H3 | 6 |
| H4 | Windows CI job (`windows-latest`, fast tier). | R1-E H4 | 2 |
| H5 | Hermetic unit tier: repo-integrity tests in their own folder, root resolved explicitly. | R1-E H5 | 1 |
| H6 | Split the `harness/engine.py` god-module (1,512 lines) into planning, dispatch, approvals and evidence. | R1-E M1 | 10 |
| H7 | Supply chain: Dependabot (pip and actions, grouped), CycloneDX SBOM, `attest-build-provenance`, cosign keyless, OpenSSF Scorecard, CodeQL. | R1-E M2/miss | 4 |
| H8 | pytest-timeout (120 s default; explicit on nightly) and `--durations=20`; fast/slow tiers. | R1-E M3 | 1 |
| H9 | One marker definition each (`slow`, `nightly`) and document the env var. | R1-E M4 | 0.25 |
| H10 | Extend the label gate to snapshots and cassettes. | R1-E M5 | 1 |
| H11 | API keys: enforce >= 32 bytes of entropy; label principals by key id. | R1-E M6 | 2 |
| H12 | `print` to logging in library code; audit the 19 `except Exception`. | R1-E L1/L2 | 2.5 |
| H13 | Coverage on one Python version only; benchmarks with a regression gate (pytest-benchmark). | R1-E L3/miss-5 | 4 |
| H14 | pre-commit (ruff, mypy, banned words, docs check). | R1-E miss-6 | 1 |
| H15 | Schemathesis fuzzing of the API from OpenAPI; OpenAPI JSON checked and diffed in the contracts job. | R1-E miss-8/9 | 4 |

### WS-I Documentation and learning
| # | Step | Tags | h |
|---|---|---|---|
| I1 | ADRs: LLM-never-computes, policy engine as authority, hashed lock, MCP per domain, VOID policy. | R1-E miss-7 | 4 |
| I2 | Generated API reference (OpenAPI plus pdoc for analytics). | R1-E miss-8 | 3 |
| I3 | Add a note to `data/reference/ATTRIBUTION.md` that the knowledge documents are fictional, written for this project, and that `classification: internal` is test metadata. (Do not edit the corpus files themselves.) | LSEG check 2026-11-09 | 0.5 |
| I4 | Docs for every workstream above, per the full-docs-review rule. | standing rule | per round |
| I5 | *(Reserved for R2-DT findings: learning path, wrong claims, glossary, exercises, how-to-extend, README length, landing page, test pyramid, mutants, metamorphic and CLI tests.)* | R2-DT | tbd |

## 3. Findings checked and not adopted
- **R1-Q M2** "ridge labels may not be demeaned": wrong. `analytics/ml_signals.py:94` demeans per date. The coefficient-stability part is kept in D12.
- **R2-MR M3** "no per-task budget": partly wrong. `CEAP_TASK_BUDGET_USD` exists (`llm/router.py:302`) but is off by default. Folded into A10.

## 4. Release train (one combined PR per release, parallel lanes)
| Release | Content | Lanes (parallel) | Est. h |
|---|---|---|---|
| 0.8.0 | Safety and correctness: WS-A (A1-A20), F9, F11, G1, G3, G6, G7, H2, H5, H8, H9, I3 | 4 | ~110 |
| 0.9.0 | Real AI evaluation and RAG: WS-B, WS-C | 3 | ~95 |
| 0.10.0 | Quant research realism: D1-D23, E1 (own prereg), E2 | 3 | ~160 |
| 0.11.0 | Execution and algos: F1-F8, F10, F12-F14, G2, G4 | 3 | ~170 |
| 0.12.0 | Platform and engineering: G5, G8-G13, H1, H3, H4, H6, H7, H10-H15, I1, I2 | 3 | ~95 |
| each | Full docs pass (I4) before the PR; full local tests with key unset before push | — | incl. |

About 630 engineering hours in all. On recent pace (3-4 parallel lanes, CI about 21 min, a slow
machine), expect roughly **2-3 wall-clock weeks**: about 3 days for 0.8.0, then 3-5 days per later
release, with reviewer time on top.

## 5. Owner decisions (all zero cost) — ALL APPROVED by the owner 2026-11-09
- **D1** Download a local open-weight model (about 5-9 GB) via Ollama or llama.cpp, plus small
  embedding and reranker models (about 0.5 GB), for B1-B5, B7 and C2. Free; disk and CPU only. On
  CPU, expect minutes per investigation.
- **D2** Free downloads for E1: Ken French industry and factor files, FRED series, AQR datasets
  (CSV/XLS, no sign-up).
- **D3** E1 preregistration signing (as before, delegated to Claude?) and the holdout opened exactly
  once.
- **D4** Accept the breaking changes in 0.8.0: the narrative template replaces failing prose; the
  policy fails closed when no universe is set; admins need approval for HIGH risk.
