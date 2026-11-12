# CEAP independent review, 2026-11-12

Reviewed: `main` at tag v0.9.0 (`dd6bf3c`), read-only, in a worktree on branch `review-2026-11-12`.
Nothing outside `docs/review/2026-11-12/` was changed. No model was called, nothing was downloaded,
nothing was spent.

## How this review was done

Seven reviewers worked independently, one per area, each with its own report in `reviewers/`:
AI-1 (LLM layer), AI-2 (agents, harness, MCP, policy), AI-3 (RAG, critic, judge, guardrails,
evaluation honesty), Q (quant research and statistics), X (execution, TCA, paper trading, ledger),
E (engineering, CI, security) and D (documentation accuracy). Each finding carries a severity, a
`file:line`, a status (verified by reading or running, judgement, or suspected) and a fix.

The coordinator then re-checked every P0 and P1 finding against the code before keeping it. The
outcome of that pass is the "Checked" column below: **re-run** means the coordinator ran it,
**read** means the cited lines were read and say what the finding says, **reviewer run** means the
reviewer executed a probe and the coordinator read the code path but did not repeat the probe.
One finding was reworded (R-12) and one was added by the coordinator (R-03). None was dropped; the
changes made during verification are listed in section 5.

Severity: P0 = a wrong number, a wrong claim or a control failure that would discredit the project
in front of an expert reader. P1 = material correctness, honesty or design gap. P2 = medium.
P3 = low.

Counts after merging duplicates: **2 P0, 33 P1, 61 P2, 18 P3** (several P3 entries are bundles of
small items). Before merging, the seven reports hold 2 P0 (one of them found twice), 35 P1, 67 P2
and 19 P3.

## 1. Executive summary

CEAP is a carefully governed research and investigation harness. Its controls are real and mostly
well built, its null and void research results are reported without spin, and its supply-chain
discipline is better than most professional codebases. Those are verified strengths, listed in
section 2.

It is not yet what its front page says about its AI and its records, and that is where a senior
reader will look first.

1. **The one end-to-end "real model" result is mock output.** The golden slice published as
   qwen2.5:7b (cause 6/9, must-mention 9/9, verdict 3/3) is produced by `MockLLMClient`. The
   cassettes hold no golden-run call, every replayed request raises `CassetteStaleError`, and the
   router falls back silently. Two reviewers found this independently and the coordinator
   reproduced it: seed 1 replay gave 0 cassette hits, 15 mock answers and exactly the published
   scores. The live per-seed files say 0/3.
2. **The forward log is not yet a forward record.** The public `forward-log` branch has one commit,
   made at 03:42 UTC on 2026-11-10, holding a row for the 10 November session that opened eleven
   hours later. Nothing has been committed since. The README says it "runs daily".
3. **Several headline AI metrics do not measure what their names say.** "Plan valid 15/30" is mostly
   one validator rule rejecting `"dataset": null`; "0/39 untraceable numbers" is a set-membership
   test that a wrong metric with a matching value passes; "tool precision 1.00" is guaranteed by the
   output grammar; judge "kappa 1.00" is agreement on five templates.
4. **A lot of what the documents describe is built but not connected.** The research realism toolkit
   (PBO, effective trial count, Ledoit-Wolf, factor regression, residualisation), the execution
   package (schedulers, cost loop, trading rules), the AI SLO monitor, the OpenTelemetry export,
   model drift checks, the challenger view and the judge have no caller in the product path.
5. **Durable runs do not resume.** No route or command re-runs a task id, a resumed run asks the
   human again, and the test that shows resumption sets the task id by hand.
6. **Two statistics in the verdict path are wrong or soft.** The Sharpe interval that decides
   INCONCLUSIVE against REJECT uses the annual-data formula on daily data, and the deflated Sharpe
   accepts any positive trial variance without a floor.

None of these is hard to fix. Most of the 0.10.0 plan is making existing claims true, wiring what is
already written, and enlarging samples. The honest summary for a reader today is: strong controls
and honest research nulls, with AI measurement and operational records that are a release behind
the prose.

### Scores against a top-firm bar

| Area | Score | Reasons |
|---|---|---|
| LLM layer (AI-1) | 4 / 10 | Good skeleton: grammar-constrained output, two-stage validation, nonce-delimited untrusted text, prompt hash pins, budget reservation, breaker. The measurement built on it does not hold: mock golden slice, a validity metric dominated by a validator quirk, digest recorded but never compared, SLO monitor unwired, local model not a runtime provider. |
| Agents, harness, MCP, policy (AI-2) | 6 / 10 | The control side is strong: the reviewer could not break approvals through the API. Durable runs and resumable approvals claim more than the code does; the standalone MCP execution server has no approval gate; the state machine records the lifecycle but does not enforce governance order. |
| RAG, critic, judge, guardrails, evaluation honesty (AI-3) | 5 / 10 | The retrieval preregistration is real (git order verified) and reproduces byte for byte; unfavourable results were kept. Everything is measured at toy scale by the system's author: 65 chunks, 15 planted faults, template-built judge labels. The ingestion screen catches 10 of 50 of the repo's own attacks. |
| Quant research and statistics (Q) | 6 / 10 | Honest nulls (E1 NULL, R5 VOID), prereg hash reproduces, expected-max Sharpe checked by Monte Carlo, Ledoit-Wolf equal to scikit-learn to 4e-16, masked Newey-West, purged walk-forward. Held down by one wrong standard error, a soft deflated-Sharpe gate, a toolkit nothing calls, no power figure at the hurdle in force and a pinned corpus file known to be wrong. |
| Execution, TCA, paper, ledger (X) | 5.5 / 10 | The maths checked is right: Perold legs sum exactly for buys and sells, Almgren-Chriss matches the paper, slice allocation is exact, auction uncross and the NYSE calendar are correct. Held down by the forward log, a keyed chain that can be re-sealed after a schema rollback, a public log keyed with the published dev key, and execution code nothing uses. |
| Engineering, CI, security (E) | 6 / 10 | Hashed lock with `--require-hashes` everywhere, SHA-pinned actions, least-privilege permissions, SBOM, provenance, signing. The 93 % coverage is not inflated by configuration. Held down by a nightly that was not green at release, a mutation ratchet that has never measured anything, a compose example that cannot start, an image that lacks the data the code looks for, and branch protection that does not require the checks guarding the evidence. |
| Documentation accuracy (D) | 6 / 10 | The 0.9.0 headline numbers match the committed JSON to the digit and nulls are stated plainly. The layer beneath is stale: the README's worked example, two recipes that fail as written, LEARN contradicting itself, hosted-model figures quoted beside "no hosted model was called". |
| **Overall** | **5.5 / 10** | A credible harness with honest research results; AI evaluation and operational evidence need one release of repair before the project is shown to expert readers. |

## 2. What is good (verified)

- **Research honesty.** E1 is preregistered and hash-locked; the prereg SHA-256 recomputes to the
  lock; the code matches the prereg; the holdout result is NULL (net t 1.16 against 1.645) and is
  stated as NULL everywhere it is summarised (`docs/research/e1/`, `README.md:173`). R5 is published
  VOID with no statistic computed.
- **Controls inside the harness.** Policy before every tool call, single-use grants, four-eyes by
  named person, approval bound to a content hash, HIGH-risk tools always gated
  (`src/ceap/policy/engine.py`, `src/ceap/policy/approvals.py`). The AI-2 reviewer tried and failed
  to forge, replay or self-approve through the API.
- **Structured output.** The Ollama adapter uses JSON-schema `format` and every planning reply in
  the cassettes is schema-valid (`src/ceap/llm/ollama.py:86-87, 100-101`).
- **Untrusted text handling.** Data blocks with a random nonce per block, NFKC and zero-width
  stripping in the prompt path (`src/ceap/llm/prompts.py:162`).
- **Retrieval evaluation process.** The rule for adopting hybrid retrieval was fixed before the run,
  the comparison reproduces from committed caches, and BM25 stayed the default when the rule said so
  (`docs/ai-agents/l3-prereg-0.9.0.md`, `docs/ai-agents/rag-hybrid-0.9.0.json`).
- **Execution maths.** Perold decomposition sums exactly; Almgren-Chriss trajectory matches the
  closed form; slice allocation conserves the parent quantity; auction uncross maximises executable
  volume (`src/ceap/analytics/implementation_shortfall.py`, `src/ceap/execution/schedule.py`,
  `src/ceap/execution/auction.py`).
- **Supply chain.** Universal hashed lock installed with `--require-hashes` in CI and Docker,
  actions pinned to commit SHAs, lock-freshness job, SBOM, build provenance and keyless signatures
  on release (`.github/workflows/ci.yml`, `.github/workflows/release.yml`, `Dockerfile:18`).
- **Coverage is real.** No omit list, 25 justified `pragma: no cover` lines, and the
  `test_cov_090_*` tests assert computed values (E report, section 3).
- **Disclosure habit.** The CHANGELOG "Known issues" block lists real defects, including the
  challenger number that was withheld rather than published from a broken path
  (`CHANGELOG.md:81-90`).

## 3. Findings ranked by severity

Source IDs point to the reviewer reports, where each finding has its full evidence, reasoning and
acceptance test. `C` marks a coordinator finding.

### P0

| ID | Source | Finding | Evidence | Checked | Fix |
|---|---|---|---|---|---|
| R-01 | AI-1-01, AI-3-01 | The local-model golden slice (cause 6/9, must-mention 9/9, verdict 3/3) is produced entirely by the mock fallback. No qwen reply is used. The explanation in the results page ("replay is the measurement of record ... the model's replies are identical in both") is false for these rows. No test pins the golden rows. | `src/ceap/eval/local_model.py:94-98`; `src/ceap/llm/router.py:404-417`; published at `README.md:19, 160`, `CHANGELOG.md:20, 67`, `docs/evaluation/local_model/README.md:44-47, 88-92`; live result `docs/evaluation/local_model/qwen2.5_7b-instruct_s1.json` (`primary_cause_accuracy: 0.0`); test `tests/evaluation/test_local_model_090.py:147-148` | Re-run: seed 1 replay gave 0 cassette hits, 6 misses, 15 mock answers, and the published 0.667 / 1.0 / 1.0 | Withdraw the rows now. Evaluation runs use no fallback; a cassette miss is an error. Refuse the `local_model` evidence class when any call fell back. Re-record a golden run with the tools pre-computed so the model is not competing for CPU. CI replay asserts every interaction is consumed and every published number reproduces. |
| R-02 | X-01, X-04 | The public forward log is one commit made at 03:42 UTC on 2026-11-10 with rows for 9 and 10 November; the 10 November row predates its session by about eleven hours. No commit since, although the schedule is 21:30 UTC on weekdays, so the 10 and 11 November scheduled runs left no record. `run_day` has no future-date guard. The README says the log "runs daily". | `origin/forward-log` (single commit `3be7b08`, `forward_log.csv`, `REPORT.txt`); `src/ceap/paper/job.py:126-135`; `.github/workflows/paper-daily.yml:10, 27`; `README.md:175`; `.github/workflows/paper-staleness.yml:10, 47` | Re-run: `git log origin/forward-log` and the CSV read by the coordinator | Refuse a session date later than the last completed NYSE close. Find out why the scheduled job produced nothing (the `PAPER_DAILY_ENABLED` variable gates it) and fix it. Restart the forward record from a clean date and say so; keep the old rows as a disclosed false start. Staleness check daily with zero misses allowed. Until then, change "runs daily" to what is true. The feed is synthetic, so no market information leaked; the failure is in the integrity of the record, which is the only thing this log exists to show. |

### P1

| ID | Source | Finding | Evidence | Checked | Fix |
|---|---|---|---|---|---|
| R-03 | C (D flagged as suspected) | One investigation report gives two values for the same quantity under the same word. T06 at v0.9.0: the summary says "implementation shortfall delta -3.7 bps ... slippage delta +0.0 bps"; observation 5 says shortfall "decreased by 28.6 bps relative to the baseline's +13.9 bps" and observation 6 says slippage "delta +0.5 bps". The attribution uses the pooled peer windows as its baseline; the execution agent uses the task baseline window. Both are called "the baseline". Each number is traceable, so the number audit and the critic's contradiction checks pass. | `src/ceap/analytics/attribution.py:319-327`; `src/ceap/agents/execution.py:21-25, 66-71`; `src/ceap/llm/client.py:618-642` | Re-run: `ceap investigate ... --dataset T06` | Name the two baselines ("matched peer windows" and "prior window") everywhere they are printed, or use one. Add a critic check: two deltas of the same metric in one report must name different baselines or agree. Golden test on T06. |
| R-04 | AI-1-02 | "Plan valid 15/30" mostly measures a validator rule. The model emits `"dataset": null` for an optional argument; the validator compares `str(None)` with the pinned value and rejects the plan; the sanitiser already treats `None` as harmless. On replay, 72 of 73 validation errors were this one. Corrected first-attempt validity is about 28-29 of 30. The result files do not store error text, so a reader cannot see this. | `src/ceap/llm/schemas.py:267-269`; `src/ceap/agents/planner.py:383-384` | Read (mechanism); count from reviewer run | Skip `None` in the validator as the sanitiser does; remove `null` from optional members in the schema; store validation errors in the result rows; republish with both the old and the corrected figure and say why they differ. |
| R-05 | AI-1-03, AI-3-03 | "Faithful claims" and "0/39 untraceable numbers" test set membership: a narrative number passes if it equals any fact value after rounding, whatever metric or cause it is attached to. A wrong metric, a wrong sign with a direction word and a wrong cause all pass. The sentence "the guardrail, not the model, is what keeps the report honest" is not supported: the model proposed no untraceable number; the 14 dropped claims failed on fact-key paths and an evidence-id namespace mismatch. | `src/ceap/agents/reporter.py:741-751, 916-942`; `src/ceap/eval/local_model.py:70-78`; `docs/evaluation/local_model/README.md:42, 51-53` | Read; examples from reviewer run | Bind each number to its fact key (the typed placeholder path already exists); rename the metrics to what they check; publish the drop reasons; add planted wrong-metric, wrong-sign and wrong-cause cases to the reporter evaluation. |
| R-06 | AI-3-02, AI-1-06 | Critic 35/45 is published without its false-alarm rate, which the same files give as 4/30. By kind, direction (sign) faults are detected 0/9. The deterministic checks score 15/15 on the same cases, so on this set the model adds nothing. | `src/ceap/eval/local_model.py:110-122`; `docs/evaluation/local_model/qwen2.5_7b-instruct_s1.json` to `_s3.json` (`false_alarm` 1/10, 1/10, 2/10); `README.md:19` | Re-run: false-alarm counts read from the three files | Publish detection, false alarms and the per-kind table together, with the deterministic baseline in the same row. |
| R-07 | AI-3-06 | Judge "kappa 1.00" for `hedged` is agreement on five templates times ten rows whose labels follow from the template; the overclaim wording ("proves beyond doubt", "no other explanation is possible") is quoted in the rubric. No human labelled anything, although the earlier plan (B5) promised 50 human labels. The judge has no runtime caller. | `src/ceap/eval/judge.py:54-59, 137-147`; grep finds no importer of `ceap.eval.judge` in `src/` outside the module; `docs/review/2026-11-09/PLAN.md:115` | Read; grep re-run | Fifty items labelled blind by the owner (free), with model-written narratives rather than templates; report kappa against those; or describe the judge as an unvalidated prototype and remove the headline. |
| R-08 | AI-3-04 | The knowledge-ingestion injection screen is six narrow regexes applied to raw text. It flags 10 of 50 of the repository's own attack strings and one zero-width character bypasses it. The prompt path normalises; this path does not. | `src/ceap/rag/ingestion.py:53-62, 110-113` | Read; recall from reviewer run | Apply the same NFKC and zero-width normalisation before matching; measure recall and false positives on the injection corpus and the clean corpus in CI; add a small classifier as an advisory tripwire (see `AI_ROADMAP.md`). |
| R-09 | AI-3-05 | The citation audit proves the quoted text is a chunk; it does not check that the chunk supports the claim. It has never run against a real model (`citation_verified` n = 0, row omitted from the results table). | `src/ceap/agents/reporter.py:999-1034, 1045-1063` | Read | Deterministic support check (shared key terms and numbers between claim and passage) now; NLI scorer when a real corpus exists; state n = 0 in the results page. |
| R-10 | AI-3-07 | The retrieval evidence rests on nine short documents (1,922 words, 65 chunks) with queries and labels written from the corpus by the system's author. The README quotes the opt-in reranker gain but not the production figure, gated recall 89/124 (typos 11/30). | `data/reference/knowledge/` (nine files; word count re-run); `data/reference/knowledge/eval.json`; `README.md:172`; `docs/ai-agents/rag-hybrid-0.9.0.md` | Re-run (word count); read | State the corpus size and the production gated recall next to the headline; build a real corpus with independent labels (0.11.0). |
| R-11 | AI-2-01, AI-2-02 | Durable runs cannot be resumed through the product. Every submission mints a new task id; no route or command re-runs one; the chaos test replaces the id by hand. A resumed run also asks the human again and writes a second case, because the approval's content hash includes fresh finding and evidence ids. The runbook and cookbook describe resumption as working. | `src/ceap/domain/tasks.py:77`; `src/ceap/platform.py:170-182`; `tests/unit/test_r090_chaos.py:150`; `src/ceap/harness/approvals.py:60-70`; `src/ceap/harness/common.py:236-245`; claims at `docs/RUNBOOK.md:28-31`, `COOKBOOK.md:960-962` | Read; approval behaviour from reviewer run | A resume entry point (`POST /tasks/{id}/resume` and a CLI command); a content hash over stable content only; a test that kills and resumes through the API without touching ids. |
| R-12 | AI-2-03 | The standalone MCP execution server stages paper orders on capability alone. Started over stdio with a role that holds `trading:execute`, `stage_orders` returns STAGED with no human approval, no four-eyes and no bound portfolio hash (the hash argument is optional). The tool description says it "requires ... human approval"; ADR 0002 says every tool call goes through the harness. Reworded on verification: the kill switch and the pre-trade checks do run on this path, and the role comes from an operator-set environment variable, so this is an unguarded second door rather than an open one. | `src/ceap/mcp/server.py:29-42, 213-217`; `src/ceap/mcp/execution/server.py:190-223`; `src/ceap/mcp/execution/__main__.py:8`; `docs/adr/0002-policy-engine-is-the-authority.md:13` | Read; staging result from reviewer run | Standalone servers refuse HIGH-risk tools unless a signed approval grant is presented, or do not register them at all; make the hash argument required; test over stdio. |
| R-13 | AI-1-04 | The model is described as pinned by digest; the code checks the tag against an allow-list. The digest is fetched for provenance, returns an empty string on any error and is never compared. The Ollama version is not in the provenance, so the disclosed mid-run upgrade cannot be checked from the artefacts. | `src/ceap/llm/ollama.py:49-51, 210-211, 320-331`; `docs/evaluation/local_model/README.md:18, 96-98` | Read | Put the digests in `model_pins.toml`; compare at client start and fail on mismatch or empty; record the Ollama version per interaction. |
| R-14 | AI-1-05 | The AI SLO monitor is not wired into the platform: `default_monitor` is never set outside the evaluation runner, and its plan-valid and repair indicators cannot see agent-level repairs, so it would show qwen at plan valid 1.00. | `src/ceap/observability/ai_slo.py:118-132, 301`; `src/ceap/llm/router.py:203, 350`; only assignment at `src/ceap/eval/local_model.py:95` | Read; grep re-run | Set the monitor in `Platform`; feed it the planner's own validity and repair outcome; test that a degraded plan moves the indicator. |
| R-15 | Q-01 | The Sharpe interval that decides INCONCLUSIVE against REJECT uses `sqrt((1 + SR_ann^2 / 2) / years)`. With daily data the Lo (2002) term is the daily Sharpe squared, so the annual Sharpe squared must be divided by 252. The interval is about 22 % too wide at Sharpe 1 and 73 % at Sharpe 2. Two other modules have the correct form. The errata repeats the wrong value (0.457; correct 0.447). | `src/ceap/analytics/research_assessment.py:116-123`; correct at `src/ceap/analytics/profitability.py:33-37` and `src/ceap/paper/forward.py:40-43`; `docs/quant/quant-errata-0.9.0.md` | Read | One shared function; a test against a simulated sampling distribution; re-run the 30 research scenarios and the golden set and publish any verdict that moves. |
| R-16 | Q-02 | The deflated Sharpe gate is soft in four ways: the trial-ledger variance replaces `1/T` whenever it is positive, with no floor, so a tiny variance moves the DSR for Sharpe 0.3 from 0.15 to 0.83; the null sweep passes `None` for the variance, so the production path is untested; the ledger records net Sharpe while the DSR deflates gross; the pass bar is 0.5. | `src/ceap/analytics/research_assessment.py:91, 164-166, 219-224`; `src/ceap/analytics/null_sweep.py:128`; `src/ceap/mcp/backtest/server.py:33` | Read; DSR values from reviewer run | Floor the variance at `1/T`; make the sweep use the production call; record gross and net consistently; justify or raise the bar (0.95 is the usual reading) and report the false-positive rate under the chosen bar. |
| R-17 | Q-03 | The research realism toolkit is unreachable: `pbo_cscv`, the effective-trial functions, everything in `risk_research.py` (Ledoit-Wolf, factor regression, residualise, robustness report) and `ModelRegistry.check_drift` have no caller outside tests; `n_trials_effective` is read but never written. The earlier plan lists these as done (D8, D9, D13, D16). | `src/ceap/analytics/overfitting.py:41`; `src/ceap/analytics/trial_accounting.py:31-84`; `src/ceap/analytics/risk_research.py:36-194`; `src/ceap/ml/registry.py:248` | Re-run: grep for callers in `src/` | Wire each into the research pipeline with a result field, or mark it as library code in the docs. Acceptance: each has a caller in the verdict path and a test through the harness. |
| R-18 | Q-04, AI-3-14, D-04 | The pinned RAG corpus serves quant facts the project knows are wrong (IC hurdle 0.40 "measured, not chosen", two verdicts, 29/30) while the errata is kept out of the corpus. The page is also linked from the site with no pointer to the errata. Already queued by the owner; listed because it affects what the product answers today. | `data/reference/knowledge/manifest.json:4`; `docs/quant/quant.md:171-196, 286-290`; `docs/quant/quant-errata-0.9.0.md:6-7`; `CHANGELOG.md:102` | Read | Fold the errata in, with R-15's corrected value, bump the corpus pin and re-measure retrieval in one change. Until then, a banner on the page. |
| R-19 | Q-05 | There is no power figure for the hurdle in force. The committed power curve is at hurdle 0.40; the gate now uses t at least 3 for mined results. From the file's own expected t, power at 3 is about 9 % at gross Sharpe 0.5 (reviewer arithmetic, not a simulation). | `docs/quant_power_curve.json` (`"hurdle": 0.4`); `src/ceap/analytics/power.py:46`; `src/ceap/analytics/research_assessment.py:81-82` | Re-run: hurdle field read | Regenerate the curve at both hurdles in force and publish it; say plainly what premium the gate can detect on this universe and horizon. |
| R-20 | Q-06 | A premium planted on market beta passes the IC part of the gate (5/8 and 8/8 at the two premium levels, the project's own numbers). The remedy, residualising on factors, exists and is not wired. Effect on the full verdict is suspected, not confirmed. | `docs/research/README.md:179-183`; `src/ceap/analytics/risk_research.py:89` | Read | Factor-neutral IC as a gate condition; re-run the adversarial family through the full verdict and publish the rate. |
| R-21 | X-02 | The keyed hash chain can be rewritten without the key. Setting the schema version below the keyed-chain version makes every row count as legacy; the next normal open runs the migration and re-seals the forged rows with the real key, after which `verify_chain()` passes. The module docstring claims the opposite. | `src/ceap/persistence/db.py:318-334`; `src/ceap/persistence/chainkey.py:21-26` | Read; attack from reviewer run (end to end) | Refuse to migrate a database that already has a `chain_epoch` row or any keyed row; store the high-water schema version under the HMAC; test the rollback attack. |
| R-22 | X-03 | The public forward log has no working tamper evidence. Its rows are keyed with the development key that is published in the source, the workflow sets `CEAP_ENV: ci`, and no anchor is ever written. The code's own comment says the dev key "protects nothing" and that anchoring covers the gap. | `src/ceap/persistence/chainkey.py:37-40`; `.github/workflows/paper-daily.yml:31` (no key, no anchor step) | Read; workflow grep re-run | Anchor the chain head in each daily commit message or a signed tag and verify it the next day; run the replay gate in the workflow; describe the log as append-only by git history, not by key, until a secret key is used. |
| R-23 | X-05 | `ceap.execution` is not on any default product path. Only the auction model and the LOB cost measurement are imported, by opt-in paper settings; the schedulers, child logic, cost loop, markouts and trading rules (tick, lot, LULD) have no importer outside the package. Documents describe the cost loop, the rules and the replay gate as active. | importers of `ceap.execution` outside the package: `src/ceap/paper/fills.py:59`, `src/ceap/paper/lob_exec.py:36` only; `src/ceap/execution/rules.py:1`; `src/ceap/harness/paper_gate.py:5` | Re-run: grep for importers | Either wire a scheduler and the rules into the paper path behind a config switch with a paired study, or label the package "simulator library, not used by the paper book" in every document. |
| R-24 | X-06 | The pre-trade cost model card reports coverage only: no point-error metric and no naive challenger. Coverage is below the nominal 90 % in 9 of 10 cells (lowest 85.9 %), and the training script accepts anything from 85 % to 95 %. The generator plants the same square-root form the model fits. Conformal intervals already exist here, so the queued "add conformal" item is a repair. | `src/ceap/ml/pretrade_model.json` (`conformal_q`, coverage cells); `scripts/train_pretrade.py:127, 152`; `docs/quant/pretrade_model_card.md` | Read (acceptance band and `conformal_q` re-run) | Report MAE and bias against a spread-only and a constant challenger; test coverage per cell against nominal with a binomial interval; fit on one generator family and test on a different functional form. |
| R-25 | E-01 | The nightly workflow was not green at release (failure on 10 November, cancelled on 11 and 12 November). The mutation job has never produced a baseline (`"survived": null`) and the ratchet passes on null; the API-fuzz job has never completed. The release check gates on CI only. The README lists "nightly mutation testing with a ratchet" as delivered; the CHANGELOG does disclose the null baseline. | `tests/mutation/baseline.json:3`; `scripts/mutation_ratchet.py:46-49`; `.github/workflows/nightly.yml:41, 65`; `scripts/release_check.py:44`; `README.md:200` | Re-run: `gh run list` and the baseline file | Fix the nightly until green; commit a measured baseline; make the ratchet fail on null after a set date; make the release check require the last nightly green. |
| R-26 | E-02, D-05 | Deployment recipes fail as written. A hash-only API key configuration is refused at start-up (`if not self.api_keys`), so the shipped compose example cannot start. The cookbook's Docker recipe uses an 8-character key that the 0.9.0 entropy rule refuses, and recipe 6 decides an approval with the admin key, whose role lacks `approvals:decide`. | `src/ceap/config.py:121, 181`; `deploy/compose/docker-compose.yml:14-18`; `COOKBOOK.md:166-167, 496`; `src/ceap/policy/permissions.py:47-63` | Read; start-up refusal from reviewer run | Accept hashed keys as sufficient; fix the recipes; a CI job that starts the compose stack and calls `/health` and one authenticated route. |
| R-27 | E-03 | The wheel and the Docker image do not contain the data the code resolves from the repository root. About 25 module constants use `Path(__file__).resolve().parents[3] / "data"`; installed into `/opt/ceap/lib/.../site-packages` that path does not exist, and the image copies `data` to `/app/data`. The knowledge base, the change detector and the calibration fall back silently. The only image test is `ceap --help`. Based on reading and path arithmetic; the image was not built. | `src/ceap/rag/retrieval.py:47`; `src/ceap/ml/registry.py:38`; `Dockerfile:22, 36-38`; `pyproject.toml:64`; `.github/workflows/ci.yml:231` | Read | One `data_root()` that honours an environment variable and package data; fail loudly when a required file is missing; an installed-wheel and container smoke test that runs one investigation and asserts the knowledge base and models loaded. |
| R-28 | E-04 | Branch protection requires nine checks and omits `golden-change` (the job that carries the snapshot and cassette label gate), `test-windows`, `benchmarks` and CodeQL. Zero reviews are required and admins are exempt. The gate that protects the evidence can therefore be skipped. | `gh api .../branches/main/protection`: checks `lint, test (3.11), test (3.12), lock-fresh, audit, secrets, docker, changelog, contracts`; reviews 0; admins not enforced; `.github/workflows/ci.yml:78, 103, 288` | Re-run: `gh api` | Owner adds the four checks and enforces for admins. A second reviewer is not available on a solo project; say so in the threat model. |
| R-29 | D-01 | The README's only worked example (and the copy on the Pages site) is not what v0.9.0 prints: -35.9 bps, ratio 9.98, reject rate 10.0 %, p99 9828 us in the document; -3.7 bps, 10.10, 9.2 %, 10008 us from the code. | `README.md:119-145`; `docs/index.html:310-325` | Re-run: the README command | Generate the example from the command in CI and fail on drift. |
| R-30 | D-02 | "Research scenarios RS01-RS30, 500-name universe" is false. The scenarios use the 30-name universe; the 500-name set is imported by one module and used by no scenario. A decile is three names, which the earlier plan (D5) set out to fix. | `README.md:154`; `src/ceap/data/historical.py:328`; `docs/review/2026-11-09/PLAN.md:135` | Read; grep re-run | Correct the line now; run the research evaluation on the 500-name universe in 0.10.0 and publish both. |
| R-31 | D-03 | Hosted-model figures are quoted in three documents while the README and the site say none exist, and they disagree with each other: 0.95 in one, 19/21 in two. There is no artefact and the cited CHANGELOG entry has neither figure. | `docs/evaluation/evaluation.md:164-167`; `LEARN.md:644-646`; `docs/ai-agents/ai-agents.md:356`; against `README.md:15` and `docs/index.html:199` | Read | Remove the figures, or commit the artefact and correct the front page. Without an artefact they are anecdotes. |
| R-32 | D-06 | LEARN and a second page still publish "21/24 clear", a count the evaluation page itself says does not add up. The 23/24 split that replaced it has no committed artefact. | `LEARN.md:662`; `docs/quant/real-data-and-paper.md:51`; `docs/evaluation/evaluation.md:145-147` | Read | Commit the per-class split in `latest.json` and source every mention from it. |
| R-33 | D-07 | The 2026-11-09 plan has no evidence trail. None of the eight review files it cites is in the repository, the eighth (R2-DT) never reported, and there is no 0.9.0 status table although three documents say there is. Two items disappeared without mention: G4 (backtest hot loop) and the per-package 95 % coverage in H2. | `docs/review/2026-11-09/` (only `PLAN.md`); `docs/review/2026-11-09/PLAN.md:39-45, 185, 200, 222`; `README.md:182`; `CHANGELOG.md:11`; `docs/INDEX.md:27` | Re-run: directory listing | Add a 0.9.0 status table; commit the review files if they still exist, or say they were not kept. This review keeps its seven reports in `reviewers/` for that reason. |
| R-34 | AI-1-12, AI-1-13, E-10 | Pieces listed as delivered that the product cannot reach: Ollama is not a runtime provider (`LLM_PROVIDERS = ("mock", "anthropic")`), so the platform cannot run on the local model outside the evaluation runner; the challenger block in the approver view has no writer; the OpenTelemetry export has no caller while the README says "OpenTelemetry-ready". Individually P2; together they are the pattern in summary point 4. | `src/ceap/config.py:29`; `src/ceap/harness/common.py:53, 276`; `src/ceap/observability/tracing.py:136, 155`; `README.md:42` | Read | A "wired or labelled" pass: every module has a caller in the product path or is listed as library-only in one table in the docs. A repo test enforces the table. |
| R-35 | C | The local-model headline omits what a practitioner needs to judge it: a planning call takes a median of about 14-19 minutes on the test machine and uses up to 8,154 of 8,192 context tokens with no overflow check; the pooled Wilson intervals treat three seeds of ten identical cases as thirty draws; and the challenger has no number. Each is disclosed somewhere or is in the JSON; none is in the headline line that the README, CHANGELOG and site share. | `docs/evaluation/local_model/qwen2.5_7b-instruct.json` (`latency_ms`); `src/ceap/llm/ollama.py:97`; `src/ceap/eval/local_model.py:139`; `README.md:19` | Read | Rewrite the headline from a generated block: cases, seeds, case-clustered interval, latency, false alarms, and what was not measured. |

### P2

Listed by area with the source ID; the evidence and fix for each are in the named reviewer report.

**LLM layer.** AI-1-07 tool precision 1.00 is true by construction (`src/ceap/llm/schemas.py:145`).
AI-1-08 planning at the context limit, no `num_predict` (`src/ceap/llm/ollama.py:97`). AI-1-09 a
seeded live run is not repeatable because each request carries fresh nonces
(`src/ceap/llm/prompts.py:162`). AI-1-10 cassettes store no request text, prompt version or commit
(`src/ceap/llm/ollama.py:137-143`). AI-1-11 the LLM kill switch is a start-up flag on the router
only; evaluation runners and a bare client bypass it (`src/ceap/config.py:129`,
`src/ceap/llm/router.py:361-362`). AI-1-14 and AI-3-13 pooled intervals over identical cases
(`src/ceap/eval/local_model.py:139`).

**Agents, harness, MCP.** AI-2-04 side-effecting calls are at-least-once and three retry layers
ignore `read_only` (`src/ceap/harness/executor.py:235-237`). AI-2-05 plan and agent results are not
journalled (`src/ceap/harness/engine.py:186`). AI-2-06 auto-approvals and timeouts stored in case
memory as human decisions (`src/ceap/harness/approvals.py:121-122`). AI-2-07 case memory has no
owner boundary, persistence or expiry (`src/ceap/harness/common.py:190-192`). AI-2-08 time waiting
for a human counts against the task deadline (`src/ceap/harness/engine.py:141, 214-215`). AI-2-09 a
resumed approval comes from an unauthenticated journal row (`src/ceap/harness/executor.py:255-262`).
AI-2-10 replay never compares prompts or the live status (`src/ceap/harness/replay.py:210-229`).
AI-2-11 the state machine allows EXECUTING to FINALISING with no guard
(`src/ceap/harness/state_machine.py:36-42`). AI-2-12 planner recall is scored on the raw reply, not
the executed plan (`src/ceap/eval/agents.py:120-138`). AI-2-13 MCP in the product path is an
in-process registry; schemas carry types only (`src/ceap/platform.py:149`).

**RAG, critic, guardrails.** AI-3-08 "rerank variants beat BM25" holds for one of two, with four
comparisons and no multiplicity control (`docs/ai-agents/rag-hybrid-0.9.0.md:28`,
`CHANGELOG.md:170`). AI-3-09 the critic held-out set is now a CI regression test and the checker
changed after first scoring. AI-3-10 planning latency absent from every headline. AI-3-11 embedding
and rerank caches pin a model name, not a weights digest (`src/ceap/rag/dense.py:39-40, 52-53`).
AI-3-12 the injection corpus is 50 strings on 4 surfaces in one context each, with non-executable
goal and surface pairs in the denominator (`src/ceap/eval/injection.py:53-72, 178-190`).

**Quant.** Q-07 adversarial study at 8 seeds a point with shared null datasets
(`src/ceap/analytics/adversarial.py:44, 93`). Q-08 E1 is a pseudo-holdout on heavily studied public
data and the lock evidence is local git time, 24 minutes before the opening
(`docs/research/e1/prereg_lock.json:5-7`). Q-09 E1 cannot be rebuilt from the repository and the
fetch script overwrites the recorded hashes (`scripts/fetch_kf_industries.py:24-28`). Q-10
registered models and the model-card header are stale against the generator hash
(`docs/ml/model_cards.md:3-4`). Q-11 the ridge combination is scored at lag 0, gross
(`src/ceap/analytics/ml_signals.py:11, 177`). Q-12 the trial ledger is per process unless an
environment variable is set (`src/ceap/analytics/trial_ledger.py:44-51`). Q-13 the PBO docstring
gives a single-run reading the estimator cannot support (`src/ceap/analytics/overfitting.py:10-11`).
Q-14 exact-zero guards: the two disclosed (`src/ceap/analytics/risk_research.py:85`,
`src/ceap/analytics/portfolio_risk.py:87`) plus three more (`src/ceap/analytics/profitability.py:122-126`,
`src/ceap/analytics/ml_signals.py:188`, `src/ceap/analytics/attribution.py:631`). Recommended
tolerance: `tss <= n * (1e-9 * max|y|)^2` with an absolute floor; the errata's mean-based version
fails for a zero-mean series.

**Execution.** X-07 the POV participation band does nothing (`src/ceap/execution/schedule.py:150`).
X-08 schedulers do not complete the parent and the last slice trades at its start
(`src/ceap/execution/algo.py:106, 113`). X-09 the LOB has no self-trade prevention and the bot can
cancel any order (`src/ceap/execution/lob.py:169`). X-10 the kill switch fails open in three places
(`src/ceap/persistence/killswitch.py:41`). X-11 no absolute order, net exposure or drawdown limits
(`src/ceap/paper/pretrade.py:171`). X-12 books of record are binary floats
(`src/ceap/paper/fills.py:137`). X-13 markout intervals treat fills as independent
(`src/ceap/execution/markouts.py:52`). X-14 interval VWAP and participation include the order's own
prints (`src/ceap/data/synthetic.py:534`). X-15 possible dividend double count on the Stooq feed
(suspected; feed not enabled; `src/ceap/paper/feeds_real.py:9`). X-16 cost-loop recalibration is
adopted with no guard and three impact coefficients coexist (`src/ceap/execution/cost_loop.py:143`).

**Engineering.** E-05 fifteen mutual package dependencies and 228 function-local imports, with no
enforcement (`src/ceap/analytics/tca_aggregate.py:43`). E-06 blocking SQLite calls on the event loop
(`src/ceap/persistence/db.py:203-209`, `src/ceap/api/routes.py:112`). E-07 the `Idempotency-Key`
check races under concurrent retries (`src/ceap/api/routes.py:112, 250, 277`). E-08 a malformed key
entry echoes the raw key in the error (`src/ceap/config.py:220`). E-09 and AI-3-15 the redactor
removes 8 to 19 digit numbers, which deletes quantities, notionals and order ids from the user's
question (`src/ceap/util/redact.py:91`, `src/ceap/domain/tasks.py:77`). E-11 the release job
installs unhashed tools while holding the signing identity (`.github/workflows/release.yml:47-61`).
E-12 strict typing covers three packages and not the safety-critical ones (`pyproject.toml:96`).
E-13 Hypothesis is derandomised in the nightly too (`tests/conftest.py:90-91`). E-14 the suite is
load-sensitive and the timeout scale treats the symptom (`docs/testing/test-pyramid.md:66-72`).
E-15 the benchmark gate misses the paths that were the performance findings
(`benchmarks/test_bench_analytics.py:32-48`).

**Docs.** D-08 LEARN contradicts itself in five places (for example `LEARN.md:715`). D-09 speed
claims that do not hold (`LEARN.md:76, 478`). D-10 broken references the checker cannot see
(`docs/ai-agents/rag-hybrid-0.9.0.md:85`, `COOKBOOK.md:257`). D-11 stale thresholds and counts
(coverage floor 85 against 90; `history.csv` has no 0.8.0 row). D-12 "hand-labelled" and "held-out"
used for things that are not (`docs/DIAGRAMS.md:578, 591`). D-13 "the last planned release" beside
work scheduled for 0.10.0 (`README.md:45`, `CHANGELOG.md:11`). D-14 the local-model summary omits
latency and the 6/9 forbidden-claim row. D-15 the golden sign-off record and the CHANGELOG disagree
on what 13/15 is (`tests/golden/golden_set_research_v1_1.json:11`). D-16 "escape impossible" beside
a measured 3/10 (`LEARN.md:543-544`). D-17 no "not investment advice" statement anywhere.

**Coverage and platform support (owner requirements raised during the review).** C-P2-1: the 90 %
floor is one global figure (`.github/workflows/ci.yml:73`, `.github/workflows/nightly.yml:39`);
there is no per-package floor and no `[tool.coverage]` section in `pyproject.toml`, so a package can
sit below 90 %. C-P2-2: Python 3.13 and 3.14 are neither declared nor tested (`pyproject.toml:10,
15-16`, `.github/workflows/ci.yml:54`), and numpy and scipy resolve to different versions on 3.11
and 3.12 (`requirements.lock:1196, 1274, 1949, 2014`).

### P3

AI-1-15 (nine small LLM-layer points). AI-2-14 property invariant 6 is vacuous
(`tests/properties/test_harness_invariants.py:93-94`). AI-2-15 agents receive the mutable run memory
(`src/ceap/harness/dispatch.py:110-115`). AI-2-16 small harness defects
(`src/ceap/harness/planning.py:369-376`). AI-3-16 the OOD golden split is still `PENDING_OWNER`.
Q-15 two Andrews and Newey-West implementations with different conventions
(`src/ceap/analytics/signal_statistics.py:159-203`). Q-16 eight small items. X-17 eleven small
items. E-16 the licence allow-list matches substrings, so "permitted" passes as MIT
(`scripts/release_check.py:118`). E-17 `fastembed` and `yfinance` sit outside the lock and any
extra. E-18 `KillablePool` blocks the event loop while terminating a worker
(`src/ceap/mcp/cpu_pool.py:82, 90`). E-19 the key-strength estimate rejects a small share of
correctly generated keys (`src/ceap/api/auth.py:30`). E-20 rate limiting covers two POST routes
(`src/ceap/api/routes.py:218, 500`). E-21 large functions with no complexity ceiling
(`src/ceap/paper/fills.py:238`). E-22 test files named after releases. E-23 the documented coverage
recipe omits `--cov-branch`. E-24 and D-18 smaller items.

## 4. The 2026-11-09 plan: promised, and where it stands

From the D report (section 5) and the area reports. "Done" means shipped; the third column is this
review's judgement of whether it does what the plan intended.

| Plan item | Shipped | Convincing |
|---|---|---|
| A1-A20 guardrails | yes | Mostly. A1 typed placeholders exist but the audit is still set membership (R-05). A17 ingestion screen is weak (R-08). A15 pins the prompt hash and the model tag, not the digest (R-13). |
| B1 local model on golden, adversarial and claims, three seeds | yes | No for golden (R-01); agent evals are real; adversarial is 40 of 200 on one seed. |
| B4 injection corpus, nightly | corpus yes | No nightly ever runs it: no workflow references Ollama. One manual subset. |
| B5 judge against 50 human labels | yes | No: labels are template-built (R-07). |
| B7 challenger agreement | wired | Not measured (disclosed); the approver hook has no writer (R-34). |
| B8 AI SLOs with OTel GenAI | code yes | Not wired (R-14). |
| C2 hybrid retrieval with reranker | yes, opt-in | Process convincing; scale not (R-10). |
| D5 500-name universe | generator only | Unused by any scenario (R-30). |
| D8, D9, D13, D16 trial accounting, PBO, Ledoit-Wolf, factor-neutral | code yes | Not wired (R-17). |
| E1 real-data study | yes, NULL | Convincing. Replication gate not run, as stated in advance. |
| F1-F8 execution simulator and algos | code yes | Not on a product path (R-23). |
| G2 generator 50x | no (1.18x) | Stated as a miss. |
| G4 backtest hot loop 5-10x | no | Dropped without mention (R-33). |
| G5 durable runs | code yes | Does not resume through the product (R-11). |
| H2 coverage 90 % plus 95 % on analytics and domain | 90 % global | Per-package part never done. |
| H3 mutation nightly with ratchet | workflow yes | Never measured (R-25). |
| I5 R2-DT findings | placeholder | The review never reported (R-33). |

## 5. Changes made during verification

- **R-02** merges X-01 and X-04. The reviewer rated X-01 P0 and X-04 P1; they describe one state of
  the forward-log branch and are kept as one P0. The coordinator notes that the feed is synthetic,
  so the early row leaked no information.
- **R-12** (AI-2-03) was reworded. The report said the standalone server stages with "no kill
  switch"; the code does check the kill switch and runs the pre-trade checks
  (`src/ceap/mcp/execution/server.py:213-220`). The missing controls are approval, four-eyes and the
  hash binding. Severity kept at P1.
- **R-03** was added by the coordinator after running the README example; the D reviewer had flagged
  it as suspected and outside its area.
- **R-04**: the mechanism was confirmed by reading; the count of 72 of 73 errors comes from the
  reviewer's replay and was not repeated, because the result files do not store error text.
- **R-21, R-08, R-16, R-26**: the numeric claims come from reviewer probes; the coordinator read the
  code paths and found them consistent but did not repeat the probes.
- **R-34 and R-35** group P2 findings that together carry a P1 message; the individual P2 entries
  remain in their reports.
- No P0 or P1 finding was dropped.

## 6. Not covered

Stated by the reviewers and not filled in by the coordinator: the body of `agents/reporter.py`
beyond the audit functions, the domain and research agents, seven of the eight MCP servers,
`data/synthetic.py` and `data/historical.py` internals, `data/real/*`, the LULD state machine in
`execution/rules.py`, `analytics/tca_aggregate.py`, `real_costs.py`, `liquidity.py`, the ten
hand-built hosted-adapter cassettes, the threat model beyond sampling, `SPECIFICATION.md`, CHANGELOG
sections before 0.8.0, and cookbook recipes 8-22 and 27-48 beyond parsing their commands. The full
test suite, `mypy src` and pip-audit were not run (collection alone took 124 s and gave 1,335 tests
against the stated 1,326; the difference was not chased). No model was called, so nothing here
re-measures the local model; every statement about its behaviour comes from replaying committed
cassettes.
