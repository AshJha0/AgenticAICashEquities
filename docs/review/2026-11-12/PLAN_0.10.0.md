# CEAP release plan after the 2026-11-12 review: 0.10.0, 0.11.0, 0.12.0

Built from `REVIEW.md` (finding IDs R-01 to R-35 and the reviewer IDs) and `AI_ROADMAP.md`.
Nothing here costs money, needs a paid API or paid data, or needs card details.

## 1. Shape

The findings are too many for one release, and they fall into a natural order.

| Release | Theme | Rule for what goes in |
|---|---|---|
| **0.10.0** | Make every published claim true | Corrections, wiring, guards and gates. No new capability. Every P0 and P1 is closed or relabelled. |
| **0.11.0** | Capability, with measurement designed first | Planner skills, typed graph with resume, a real retrieval corpus, groundedness, execution on a product path. Each item has a baseline and a pre-set acceptance number. |
| **0.12.0** | Depth | Paired re-record gate, memory A/B, generated attacks, a volatility forecasting study, a more credible simulator. |

One combined PR per release. Lanes push branches only; a coordinator integrates on `int-0.10.0`.
Full local tests finish before any push. A full docs pass is the last lane of each release.

### Effort and time

Effort is in plan-days (one focused engineer-day). Wall-clock uses the calibration from earlier
releases: about 12-15 plan-days per wall-clock day with 2-3 parallel sessions. Local model runs,
CI time and owner actions are listed separately because they do not compress with more sessions.

| Release | Plan-days | Wall-clock (sessions) | Local model compute | Owner time |
|---|---|---|---|---|
| 0.10.0 | 80 | 6-7 days | about 30 CPU-hours (re-record 3 seeds, golden run, challenger, temperature-0 pass) | about 2 hours (decisions, branch protection, 50 judge labels if chosen) |
| 0.11.0 | 62 | 5 days | about 40 CPU-hours | about 1 hour, plus labelling time if the owner is second labeller |
| 0.12.0 | 44 | 3-4 days | about 30 CPU-hours | under 1 hour |

For comparison, 0.9.0 was planned at about 95 engineering hours and took three wall-clock days with
six lanes plus two days of model runs and suite repair. The estimate above assumes the same pattern:
the model runs, not the coding, set the end date.

## 2. Release 0.10.0: make every claim true

### Lanes

Six lanes can run in parallel. Files do not overlap between lanes except where a dependency is
shown; the docs lane runs last.

| Lane | Workstream | Main directories | Plan-days |
|---|---|---|---|
| L1 | WS-T truth in AI evaluation | `src/ceap/eval`, `src/ceap/llm`, `tests/evaluation`, `docs/evaluation` | 19 |
| L2 | WS-H harness, MCP and guardrail edges | `src/ceap/harness`, `src/ceap/mcp`, `src/ceap/rag/ingestion.py`, `src/ceap/agents/reporter.py` | 14 |
| L3 | WS-Q quant corrections and wiring | `src/ceap/analytics`, `src/ceap/ml`, `docs/research`, `docs/quant` | 14 |
| L4 | WS-X records, ledger and execution labelling | `src/ceap/paper`, `src/ceap/persistence`, `.github/workflows/paper-*.yml` | 11 |
| L5 | WS-E engineering, coverage, packaging | `pyproject.toml`, `.github/workflows`, `Dockerfile`, `deploy`, `tests` | 15 |
| L6 | WS-D documentation (after L1-L5) | all docs | 7 |

### WS-T: truth in AI evaluation (lane L1)

| ID | Item | Closes | Days | Depends | Acceptance test |
|---|---|---|---|---|---|
| T1 | Evaluation runs use no fallback: a cassette miss raises. The runner refuses to write `evidence_class: local_model` if any call in the run was answered by a fallback. | R-01 | 1 | – | Replaying the 0.9.0 cassettes through the golden path fails with a named error; a unit test with a forced fallback asserts the refusal. |
| T2 | Withdraw the golden-slice rows from the results page, README, CHANGELOG headline and site; state why. | R-01 | 0.5 | – | `git grep "golden slice cause 6/9"` returns nothing outside the review folder and the CHANGELOG correction note. |
| T3 | CI replay gate: every recorded interaction is consumed, none is missing, and every number in the committed result JSON is reproduced. | R-01, AI-1 6(d) | 2 | T1 | A deliberately edited result file fails CI; an unused interaction fails CI. |
| T4 | Validator and sanitiser agree on `None`; optional members lose `null` in the schema; windows become an enum and dataset and symbol become constants. Validation errors are stored in result rows. | R-04 | 2 | – | Re-scoring the 0.9.0 planner cassettes gives first-attempt validity of at least 27/30 with the error list attached; a test feeds `{"dataset": null}` and expects a valid plan. |
| T5 | Number audit binds each number to its fact key; rename the two metrics to what they check; publish drop reasons. Add planted wrong-metric, wrong-sign and wrong-cause reporter cases. | R-05 | 3 | – | The three planted kinds are rejected (0 of N pass); the 0.9.0 examples from the AI-3 report are in the test file. |
| T6 | Results table shows, per model metric: detection, false alarms, per-kind breakdown, the deterministic baseline, a case-clustered bootstrap interval, and call latency. Generated from JSON into one block shared by README, CHANGELOG and site. | R-06, R-35, AI-1-14 | 2 | T3 | The block is produced by a script; `check_docs.py` fails when a copy differs. |
| T7 | Model digests in `model_pins.toml`, compared at client start (fail on mismatch or empty). Cassette manifest: request text, prompt id and version, commit, Ollama version. | R-13, AI-1-10 | 2 | – | A wrong digest refuses to start; every new interaction carries the five fields. |
| T8 | Re-record: three seeds plus a temperature-0 pass plus a real golden run with tool results pre-computed (so the model does not compete for CPU), all on the fixed path. | R-01, R-04 | 1.5 + about 20 CPU-hours | T1, T4, T7 | New result files pass T3; the page states cases, seeds and what moved against 0.9.0. |
| T9 | Challenger re-run on the fixed comparison path; publish plan agreement or say it failed again and why. (Queued by the owner.) | known issue, B7 | 1 + about 6 CPU-hours | T1, T4 | A number with its interval in the results file, reproduced by T3. |
| T10 | Judge: either 50 blind owner labels on model-written narratives with kappa reported (decision O3), or relabel as an unvalidated prototype and remove the headline. | R-07 | 1 | O3 | The results page states who labelled what; `enabled_dimensions()` reads the new file. |
| T11 | AI SLO monitor set in `Platform` and fed the planner's validity and repair outcome; one GenAI client span per routed call under the agent span; `ollama` accepted as a provider with CPU-seconds as cost. | R-14, R-34 | 3 | – | In-memory exporter test asserts span attributes (model, tokens, finish reason, temperature, seed); a degraded plan moves the indicator; `CEAP_LLM_PROVIDER=ollama` runs one investigation against a cassette. |

### WS-H: harness, MCP and guardrail edges (lane L2)

| ID | Item | Closes | Days | Depends | Acceptance test |
|---|---|---|---|---|---|
| H1 | Resume entry point (`POST /tasks/{id}/resume`, `ceap resume <id>`); plan and agent results journalled; approval content hash over stable content only. | R-11, AI-2-05 | 4 | – | Kill -9 mid-run, resume through the API with no id edited: zero repeated tool or model calls, no second approval request, one case written. |
| H2 | Standalone MCP servers do not register HIGH-risk tools unless a signed approval grant is presented; the portfolio hash argument is required. | R-12 | 1 | – | Over stdio with the admin role, `stage_orders` without a grant is refused; contract test added. |
| H3 | Ingestion screen normalises (NFKC, zero-width) before matching; recall and false-positive rate measured in CI on the injection corpus and the clean corpus. | R-08 | 1 | – | Recall on the 50 base strings is reported in the results file with an interval; the zero-width bypass test fails before and passes after. |
| H4 | Citation support check (shared key terms and numbers between claim and passage); results page states n for citation verification. | R-09 | 1.5 | – | Planted right-chunk and wrong-chunk pairs: wrong-chunk citations are rejected. |
| H5 | Critic check: two deltas of one metric in a report must name different baselines or agree; the two baselines get distinct names in every printed line. | R-03 | 1.5 | – | T06 golden: summary and observations use named baselines; the check fires on a planted contradiction. |
| H6 | Case memory records human decisions only; owner boundary; expiry. Human wait does not count against the task deadline. | AI-2-06, -07, -08 | 2 | H1 | Auto-approved and timed-out decisions are absent from memory; a 10-minute simulated wait does not time the task out. |
| H7 | Side-effecting tools receive the idempotency key; retries respect `read_only`. | AI-2-04 | 1.5 | – | A retried `stage_orders` produces one staged set under an injected failure after the write. |
| H8 | State machine guards: `finalise` requires the critic to have run and evidence to be validated; dispatch has an `else`. | AI-2-11 | 1.5 | – | Property test: no path reaches FINALISING without both flags. |

### WS-Q: quant corrections and wiring (lane L3)

| ID | Item | Closes | Days | Depends | Acceptance test |
|---|---|---|---|---|---|
| Q1 | One Sharpe standard-error function (Lo 2002, daily form); remove the wrong copy; re-run the 30 research scenarios and both golden sets; publish any verdict that moves. | R-15 | 1.5 | – | Simulated sampling distribution test (coverage of the 95 % interval within 93-97 % over 2,000 draws); a table of moved verdicts, even if empty. |
| Q2 | Deflated Sharpe: variance floored at `1/T`; the null sweep calls the production path; gross and net recorded consistently; the bar justified with its false-positive rate (decision O5). | R-16 | 2 | – | The reviewer's case (Sharpe 0.3 with a tiny ledger variance) no longer passes; the sweep's false-positive rate is published for the chosen bar. |
| Q3 | Zero-variance tolerance: `tss <= n * (1e-9 * max|y|)^2` plus an absolute floor, in all five places. (Queued by the owner; three more sites than disclosed.) | Q-14 | 1 | – | Constant `y` gives R² = NaN with a reason; a near-constant market gives a flagged beta; a zero-mean series is handled (the errata's version is not). |
| Q4 | Wire the realism toolkit into the research pipeline: PBO and effective trial count on the 24-trial matrix, factor-neutral IC as a gate condition, Ledoit-Wolf predicted volatility in the risk step. Each becomes a result field. | R-17, R-20 | 4 | Q2 | Each function has a caller in the verdict path and a harness-level test; the market-beta adversarial family is re-run through the full verdict and its pass rate published. |
| Q5 | Power curve at the hurdles in force (1.645 and 3.0), by simulation. | R-19 | 1 | – | `docs/quant_power_curve.json` carries both hurdles; the docs state the detectable premium. |
| Q6 | Research evaluation on the 500-name universe, published next to the 30-name result. | R-30 | 2 | Q1 | Both results in `latest.json`; the README line matches the code. |
| Q7 | Model registry: drift check called at load; a test fails when a registered model's generator hash differs from the current one; retrain and fix the model-card header. Trial ledger persistent by default. | Q-10, Q-12, roadmap | 1.5 | – | Stale-hash test; ledger survives a process restart. |
| Q8 | Fold the quant errata (with Q1's corrected value) into `quant.md`, bump the corpus pin and re-measure retrieval in the same change. (Queued by the owner.) | R-18 | 1 | Q1, L1 T3 | Retrieval results regenerate; the errata file is removed; cassettes and goldens that move are listed with the `snapshot-change` label. |

### WS-X: records, ledger and execution labelling (lane L4)

| ID | Item | Closes | Days | Depends | Acceptance test |
|---|---|---|---|---|---|
| X1 | `run_day` refuses a session later than the last completed NYSE close; find and fix why the scheduled job left no commit; restart the forward record from a clean date with the earlier rows kept and disclosed (decision O4). | R-02 | 2 | O4 | A future date is refused; five consecutive scheduled sessions appear on the branch before the release tag. |
| X2 | Anchor the chain head in every daily commit; verify yesterday's anchor before appending; replay gate in the workflow; staleness check daily with zero misses. | R-22 | 2 | X1 | A rewritten history fails the next run; the staleness job opens an issue after one missed session. |
| X3 | Migration refuses a database that already holds keyed rows or a `chain_epoch`; schema high-water mark under the HMAC. | R-21 | 1.5 | – | The rollback attack from the X report fails `verify_chain()`. |
| X4 | Pre-trade model card: MAE and bias against constant and spread-only challengers; coverage per cell against nominal with a binomial interval; test on a different functional form from the one fitted. | R-24 | 2.5 | – | The card shows the three tables; the training script fails when any cell's interval excludes nominal. |
| X5 | Label `ceap.execution` truthfully in every document as a simulator library not used by the default paper book; fix the POV band and the scheduler completion so the library is correct. Wiring is 0.11.0. | R-23, X-07, X-08 | 2 | – | A parent order is completed over 40 seeds; the POV band changes the schedule; a docs test checks the label. |
| X6 | Known issues from 0.9.0: lot size above the session cap, zero sigma band, Andrews lag on constant residuals, idempotency key for non-JSON arguments. | CHANGELOG known issues | 1 | – | One test each. |

### WS-E: engineering, coverage and packaging (lane L5)

| ID | Item | Closes | Days | Depends | Acceptance test |
|---|---|---|---|---|---|
| E1 | **Coverage at least 90 % branch per package** (owner requirement). Add `[tool.coverage]`; a script reads the JSON report and fails when any `ceap.*` package is under 90 % (95 % for `analytics` and `domain`, the unfinished part of the earlier plan). Measure first, then write behaviour tests for each package under the floor. | C-P2-1, earlier H2 | 6 | – | The per-package table is printed in CI and committed in `docs/testing/`; CI fails if any package drops below its floor; new tests assert outputs, not only execution (spot-checked by mutation on the touched modules). |
| E2 | Nightly green; mutation baseline measured and committed; ratchet fails on null; Hypothesis randomised in the nightly; release check requires the last nightly green. | R-25, E-13 | 2 | – | Three consecutive green nightlies before the tag; baseline is a number. |
| E3 | `data_root()` with an environment override and package data; fail loudly on a missing required file; installed-wheel and container smoke test that runs one investigation and asserts the knowledge base and models loaded. | R-27 | 2.5 | – | The smoke job fails on the 0.9.0 image and passes after. |
| E4 | Hashed keys alone are a valid configuration; compose stack started in CI with `/health` and one authenticated call; cookbook recipes fixed. | R-26 | 1.5 | – | The compose job is green. |
| E5 | "Wired or labelled" table: every module has a caller in the product path or is listed as library-only; a repo test enforces it. Delete the tracer export if T11 does not use it. | R-34 | 1 | L1 T11 | The test fails on a new uncalled module. |
| E6 | Python 3.13 in the CI matrix and classifiers; assess 3.14 (blocked only by a dependency without wheels). Pin goldens to one interpreter or prove equality across them. | C-P2-2 | 1.5 | – | `test (3.13)` green; a note on 3.14 with the blocking package named. |
| E7 | Redactor no longer removes quantities, notionals and order ids; raw key never echoed in an error. | E-08, E-09 | 0.5 | – | "Buy 12000000 shares" survives redaction; the malformed-key error contains no key material. |

### WS-D: documentation (lane L6, after the others)

| ID | Item | Closes | Days | Acceptance test |
|---|---|---|---|---|
| D1 | README example generated from the command in CI; scenario table corrected; hosted-model figures removed or sourced; "runs daily", "pinned by digest", "resumable", "OpenTelemetry-ready" and "last planned release" corrected to what is true after this release. | R-29 to R-32, D-13 | 2 | `check_docs.py` gains a generated-block check; the listed phrases are each tied to a test or removed. |
| D2 | LEARN contradictions, stale counts, broken references, misuse of "hand-labelled" and "held-out", "escape impossible". | D-08 to D-12, D-16 | 2 | The D report's number audit re-run with zero mismatches. |
| D3 | A 0.9.0 and 0.10.0 status table for the earlier plan, including the two dropped items; a statement on the missing review files; a "not investment advice" line. | R-33, D-17 | 1 | The status table exists and every row cites a commit or a CHANGELOG line. |
| D4 | Full docs pass: README, LEARN, COOKBOOK, architecture and diagrams, per-package docs, CHANGELOG. | standing rule | 2 | Banned-word check and docs check green. |

### 0.10.0 dependencies

```
T1 -> T3 -> T6
T1, T4, T7 -> T8 (model run) -> T9
Q1 -> Q6, Q8        Q2 -> Q4
H1 -> H6            O4 -> X1 -> X2
T11 -> E5           L1..L5 -> L6
```

The critical path is T1, T4, T7, then the model runs in T8 and T9 (about 26 CPU-hours on the
current laptop, which cannot run tests under load at the same time; see lesson below), then T6 and
the docs lane.

### 0.10.0 release gates

1. Every R-ID from R-01 to R-35 is closed by a merged item or relabelled in the docs, with the list
   in the PR description.
2. CI green including the new replay gate, the per-package coverage gate, the compose and wheel
   smoke tests, and `test (3.13)`.
3. Three consecutive green nightlies with a numeric mutation baseline.
4. ~~Five consecutive forward-log sessions written by the scheduled job, each anchored.~~ Moved to
   0.11.0 by decision O12. In its place: the docs state the forward log as "restarted 2026-11-12, N
   sessions recorded so far" (never "runs daily"), and the integrator reads `forward-log-v2` once
   before tagging and reports the session count and any gap in the PR. R-02 is listed as fixed
   (cause, guard) and relabelled (continuity proof moved to 0.11.0).
5. The D reviewer's number audit re-run with zero mismatches.

## 3. Release 0.11.0: capability, measured

| ID | Item | Days | Baseline and pre-set acceptance |
|---|---|---|---|
| A1 | Planner skills: the model chooses one of a few named, versioned procedures plus an enumerated scope; the harness expands it. Free-form planning stays as the challenger. | 5 | Against the 0.10.0 re-record: first-attempt validity at least 27/30, recall above the re-measured baseline, no loss on golden cause; registered before the run. |
| A2 | Shorter planning prompt with a stable prefix (tools for the task kind only, compact schemas). | 2 | Input tokens and median latency halved against 0.10.0; recall not lower. |
| A3 | Thin typed graph: a node and edge table with a guard per edge drives `execute`; a checkpoint per node; approval as a persisted interrupt. | 7 | Kill-at-every-node test: zero repeated calls and an equal normalised report at every boundary; all three governance invariants enforced by the table. |
| A4 | Planner and trajectory metrics: per-question precision, argument exact match, model-originated share of executed steps, and the model-plan against canonical-plan counterfactual. Thirty or more gold cases on at least three symbols. | 4 | Published with case-clustered intervals; the counterfactual states whether the model's plan beats the template. |
| A5 | SEC EDGAR corpus: fetcher with a declared contact (decision O6), sections, table chunking, raw files in a git-ignored cache with hashes committed. Query set: at least 300 answerable and 100 unanswerable questions, a second labeller on 20 % with agreement reported. | 8 | The five retrieval variants and the existing pre-registered rule, re-run on the new set. |
| A6 | Fuzzy lexical matching in BM25, then the reranker with a gated production row. | 2 | Typo recall against 11/30 gated; the reranker is kept only if it beats fuzzy BM25 under the same rule. |
| A7 | NLI groundedness scorer calibrated on planted pairs; context precision and nDCG from section labels. | 3 | Entailment rate of kept policy claims with an interval; calibration accuracy on about 150 planted pairs. |
| A8 | Small injection classifier as an advisory tripwire (decision O7). | 2 | Recall and false-positive rate against the regex screen measured in 0.10.0. |
| A9 | Hypothesis fuzzing of plans and claims against the harness invariants. | 2 | No invariant violation over a fixed budget; every violation found becomes a regression test. |
| A10 | MCP output schemas declared and validated; stdio contract suite in CI. | 4 | Share of tool results validated against a schema goes from 0 % to 100 % for the eight servers. |
| X7 | A scheduler and the trading rules on the paper path behind a config switch; paired scheduler study (TWAP, VWAP, POV, IS) against the Almgren-Chriss trajectory with common random numbers. | 6 | Paired differences with intervals; the document says plainly if the simulator cannot separate the algos. |
| X8 | Absolute order, net exposure and drawdown limits; kill switch fails closed in the three places found. | 3 | One test per limit; the three fail-open cases fail closed. |
| Q9 | Adversarial study at 40 seeds a point with independent null datasets; E1 rebuildable from recorded hashes (the fetch script verifies, never overwrites). | 4 | The rebuild script reproduces `result_holdout.json` from a clean cache or fails naming the changed file. |
| E8 | import-linter contract for the package layering; strict typing extended to `harness`, `policy`, `persistence` and `paper`. | 5 | The contract is green; `mypy src` clean under the wider strict set. |
| E9 | Release job split into build and sign; tools hashed; reproducible wheel; tag tied to a green main. | 3 | Two builds of the same commit produce the same wheel hash. |
| D5 | Full docs pass. | 2 | As 0.10.0. |

Lanes: (A1, A2, A4), (A3, A10), (A5, A6, A7), (A8, A9, X8), (X7, Q9), (E8, E9), then D5.
Total about 62 plan-days.

## 4. Release 0.12.0: depth

| ID | Item | Days | Note |
|---|---|---|---|
| B1 | Paired re-record gate on the owner's machine: new cassettes are accepted only if a paired case-level test against the old ones clears a margin fixed in advance. A local scheduled task pushes signed results; CI verifies by replay. No self-hosted runner on a public repository. | 4 | Needs the larger case set from A4. |
| B2 | Memory A/B (leave one out on the golden set) and a poisoning test; remove the feature if it shows nothing. | 3 | |
| B3 | Generated red-team attacks with the challenger as generator; executable-only denominators. | 3 | About 12 CPU-hours per 200 attacks. |
| B4 | Local cascade (qwen, then llama on a semantically invalid plan, then template), only if T9 and A1 leave room for it. | 3 | Decide from the 0.11.0 numbers. |
| B5 | Volatility forecasting study: a local time-series foundation model against EWMA, GARCH(1,1) and HAR on the 49 industries, QLIKE loss, Diebold-Mariano, with a live window after the model's cut-off as the only clean test. Preregistered, including the contamination caveat. | 6 | Decision O8 (model download). |
| B6 | Conformal intervals for signals, after the pre-trade repair has shown the method done properly. | 3 | |
| B7 | A more credible execution simulator check: calibrate the stylised book against one free sample day of order-book data that needs no account, and report where it fails. | 6 | Only if such a sample is available without sign-up; otherwise document the limit and stop. |
| B8 | Money as integers in the books of record, not only in hashed bodies. | 5 | Moves goldens; own lane. |
| B9 | Backtest hot loop (the dropped G4) and generator speed (G2), with the golden-moving decision put to the owner. | 6 | Decision needed on moving owner-signed goldens. |
| B10 | Preregistered live-window test for E1 (2016 onwards), the only data not seen by the literature the study follows. | 3 | One opening, ledgered. |
| D6 | Full docs pass. | 2 | |

Total about 44 plan-days.

## 5. Owner decisions

Each is a choice with a recommendation. None involves spend.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| O1 | Scope of 0.10.0 | (a) truth-first as planned, capability in 0.11.0; (b) mix capability into 0.10.0 | **(a)**. New numbers built on the current measurement would have the same defects. |
| O2 | The wrong golden-slice numbers in the tagged v0.9.0 | (a) correct on `main` now with a short CHANGELOG note, ahead of 0.10.0; (b) wait for 0.10.0; (c) also issue 0.9.1 | **(a)**. It is a docs-only change and it is the first thing an expert reader would find. This review does not make the change; the owner decides. |
| O3 | Judge labels | (a) the owner labels 50 model-written narratives blind (about 1 hour); (b) relabel the judge as an unvalidated prototype | **(a)** if the judge is to stay in the headline; otherwise (b). |
| O4 | Forward log | (a) restart from a clean date, keep the two rows as a disclosed false start; (b) delete the branch and start again; (c) keep as is and only fix forward | **(a)**. Deleting history is the wrong habit for a record whose point is integrity. Also needs the owner to confirm the `PAPER_DAILY_ENABLED` repository variable. |
| O5 | Deflated Sharpe pass bar | (a) keep 0.5 and publish its false-positive rate; (b) raise to 0.95 | **(b)**, with the rate published either way. Expect more REJECT and INCONCLUSIVE verdicts; report what moves. |
| O6 | SEC EDGAR contact string (0.11.0) | (a) the owner supplies a contact for the User-Agent, read from an environment variable and never committed; (b) no EDGAR corpus | **(a)**. The tool must not invent or embed a contact. |
| O7 | Injection classifier download (0.11.0) | (a) a small injection classifier, free, one download; (b) regex screen only | **(a)**, advisory only. |
| O8 | Time-series model download (0.12.0) | (a) approve one open model for the volatility study; (b) skip the study | Decide after 0.11.0. |
| O9 | Branch protection | (a) require `golden-change`, `test-windows`, `benchmarks`, CodeQL and enforce for admins; (b) leave | **(a)**. Owner action in repository settings. |
| O10 | Python versions | (a) add 3.13 now, 3.14 when dependencies ship wheels; (b) stay on 3.11 and 3.12 | **(a)**. |
| O11 | Signed goldens moved by Q1, Q2 and Q8 | (a) accept moved goldens with a listed diff and re-sign; (b) keep old thresholds | **(a)**. The corrections are to formulas, not to thresholds chosen after seeing results; the diff is published. |

### Decisions taken, 2026-11-12

The owner agreed with the recommendations above and delegated the decisions to the maintainer
session on 2026-11-12. Recorded outcome:

| # | Outcome | Note |
|---|---|---|
| O1 | (a) truth-first | Capability work waits for 0.11.0. |
| O2 | (a) done | PR #15 (`fix/0.9.0-headline-corrections`): docs-only correction of the golden-slice and forward-log claims. Merge is the owner's. |
| O3 | (b) for 0.10.0 | The judge is relabelled an unvalidated prototype and leaves the headline. Option (a) needs an hour of the owner's own labelling and cannot be delegated; if the owner offers it, T10 switches to (a). |
| O4 | (a) restart, keep the two rows as a disclosed false start | `PAPER_DAILY_ENABLED` was read on 2026-11-12 and is `true`, so the scheduled job is enabled and still left no commit on 10 or 11 November. X1 starts by reading those workflow runs. |
| O5 | (b) raise the bar to 0.95 | Publish the false-positive rate and every verdict that moves. |
| O6 | (a), deferred to 0.11.0 | The owner supplies the contact string when A5 starts; nothing is embedded before then. |
| O7 | (a), advisory only | One free download when A8 starts. |
| O8 | open | Decide after 0.11.0, as recommended. |
| O9 | (a), owner action | Repository settings are the owner's to change; the session does not edit branch protection. Until it is done, the integrator runs the four extra checks before every merge and says so in the PR. |
| O10 | (a) | Python 3.13 in 0.10.0; 3.14 when wheels exist. |
| O11 | (a) | Moved goldens are listed with their diff and re-signed under the standing delegation, with the `snapshot-change` label. |
| O12 | Skip the forward-log gate for 0.10.0 (owner, 2026-11-12) | X1 merged in PR #16 (job fetch, completed-session guard, restart on `forward-log-v2`). The five-session gate moves to 0.11.0. Two habits kept: honest wording in every doc (D1), and a look at `forward-log-v2` before tagging. X2 ships in the combined PR. |

Still reserved to the owner: merging PRs if the merge is blocked for the session, branch protection
(O9), the EDGAR contact (O6), any model download beyond those listed, and any spend (none planned).

## 6. Lessons carried into the plan

- **Model runs and tests do not share the laptop.** In 0.9.0 the model's CPU load timed out the
  deterministic tools and produced the incomplete live golden run. T8 pre-computes tool results and
  runs the model alone; no test suite runs during a recording.
- **A fallback must never be silent in a measurement.** T1 is first for that reason.
- **A number is published from a generated block or not at all.** T6 and D1.
- **"Done" means reachable.** E5 makes that a test.
- **Pinned corpus files move many tests.** Q8 is one change with its re-measurement, not an edit.
- **Finish local tests before pushing; one combined PR per release; check usage at each lane report
  and stop before the weekly limit.** Standing owner rules.

## 7. Items the owner queued before this review

| Item | Where it is in this plan |
|---|---|
| Fold the quant errata into `quant.md` at the corpus re-measurement | Q8 (with the corrected Sharpe value from Q1) |
| Re-run the challenger to measure agreement | T9 |
| Tolerance on the R² and beta zero-variance checks | Q3 (five sites, not two) |
| Coverage at least 90 % per package | E1 |
