# Changelog

Every pull request adds a line under `## Unreleased` (CI enforces it unless the PR is labelled
`no-changelog`). At release time the section is renamed `## [X.Y.Z] - date`; the release gate
refuses a tag whose version has no section.

## Unreleased

### Changed: the rubric judge is an unvalidated prototype (0.10.0 item T10, finding R-07, decision O3)

- The 50 calibration labels were written by Claude, not by independent raters, so agreement with
  them does not validate the judge. `enabled_dimensions()` now enables a dimension only when the
  calibration file says its labels come from independent human raters; the committed file says
  Claude, so no dimension is enabled and the judge leaves the headline. The results page and README
  say who labelled what. Owner labels (option a) would re-enable it in a later release.

### Fixed: the claims audit binds each number to the claim's own fact keys (0.10.0 item T5, finding R-05)

- **What was wrong.** `validate_claims` passed a number if it equalled any fact under any key, so a
  real figure attached to the wrong metric, a wrong direction word or a wrong cause all passed
  (the reviewer's four examples in AI-3-03). "Faithful claims" and "0/39 untraceable numbers" were
  therefore set-membership counts, and the sentence "the guardrail, not the model, is what keeps the
  report honest" was not supported. It is withdrawn from the results page.
- **The audit now** traces each number only to facts under the claim's own `fact_keys`; if the
  clause names a metric, the fact must hold that metric; a direction word must agree with the sign
  of the fact's move ("improved" is down for a cost); a cause stated as the cause must be the
  primary, and any cause token must be one that was scored (`audit_claim_numbers`,
  `audit_claim_cause` in `src/ceap/agents/reporter.py`). The free-text narrative backstop
  (`audit_numbers`) is unchanged.
- **Renamed metrics.** `reporter.faithfulness` is published as `reporter.claims_kept_by_audit` and
  `claims.untraceable_number` as `claims.number_not_in_own_facts` (`RATE_PATHS` in
  `src/ceap/eval/local_model.py`). The 0.9.0 result files keep the old keys until re-recorded.
- **Drop reasons published.** `python -m ceap.eval.local_model --replay --claims-audit` re-audits the
  recorded claims with no model call and writes
  `docs/evaluation/local_model/qwen2.5_7b-instruct_claims_audit.json`: claims kept 22/39 (25/39
  under the 0.9.0 audit); 11/39 carry a number outside their own fact keys (seven cite a path that
  does not resolve, four quote a policy threshold without citing it); every drop reason by category.
- **Planted cases.** `planted_audit_claims` (in `src/ceap/eval/clients.py`) builds wrong-metric,
  wrong-sign and wrong-cause claims from each case's real figures; all 12 are rejected, each for its
  own reason. Tests: `tests/agent/test_t5_claim_audit.py`, including the four 0.9.0 examples.
- **Changed recorded result.** The slow replay test in `tests/evaluation/test_local_model_090.py`
  now compares the reporter's kept share with the re-audit file. Seed 3 moves from 11/14 to 8/14
  kept, so its prose request is no longer in the 0.9.0 cassette; that case is a strict xfail until
  the T8 re-recording.
  Results page, README, site, LEARN, COOKBOOK and lessons 3 and 7 use the new names and figures.

### Fixed: the golden-slice withdrawal is complete and pinned (0.10.0 item T2, finding R-01)

- The rows were withdrawn from the README, the results page and the site on 2026-11-12 (see the
  correction note below). One sentence was left: lesson 6 still said that live and replayed runs
  give identical model replies, so replay is the measurement of record. For the golden slice that
  is false; the replayed answers were mock fallback output. The lesson now says replay is the
  measurement of record for the planner, reporter and critic rows only, and why no golden number
  is published.
- `tests/repo/test_t2_golden_slice_withdrawn.py` fails if "golden slice cause 6/9", or the false
  replay explanation, appears anywhere outside `docs/review/` and this file.
### Added: "wired or labelled" table and its repo test (0.10.0 item E5, finding R-34)

- `scripts/wired_or_labelled.py` follows imports from the product entry points (`ceap.cli`,
  `ceap.api`, `ceap.platform`, each MCP server's `__main__`). Each of the 23 modules none of them
  reaches is listed in section 3.13 of `docs/architecture/overview.md` as `library-only` (16) or
  `script-only` (7). The `ceap.execution` simulator modules are counted library-only.
- `tests/repo/test_wired_or_labelled.py` fails on an uncalled module with no row and on a row
  for a module that is wired or gone. Reached means importable from the product, not that every
  function is called.
- The OpenTelemetry tracer export was already removed under T11, which does not use it.

### Changed: AI SLO monitor wired into the platform; GenAI spans; Ollama as a provider (0.10.0 item T11, findings R-14, R-34)

- **R-14.** `Platform` now owns one `AISLOMonitor` and sets it on its router. The planner reports
  each plan under a new `planner` series: valid only when the first reply was usable as is, so a
  repaired or fallback plan moves the indicator. The per-call `planning` series is unchanged.
- Each routed model call is one `gen_ai.chat:<model>` span under the agent span that made it, with
  OpenTelemetry GenAI attribute names (model, token counts, finish reason, and temperature and seed
  where the client has them).
- **R-34.** `CEAP_LLM_PROVIDER=ollama` is accepted. `CEAP_OLLAMA_CASSETTE` replays a recorded
  cassette; otherwise the live model runs only with `CEAP_LOCAL_MODEL=1`. There is no mock
  fallback. Cost is recorded as `cpu_seconds` (wall-clock seconds of each local call), not dollars.
- Removed `OpenTelemetryTracer` and `build_tracer()`: nothing in the product called them. The
  README no longer says "OpenTelemetry-ready"; cookbook recipe 25 now reads the GenAI spans from
  the trace API. The `[observability]` extra still lists the OpenTelemetry packages until the lock
  file is regenerated.
- Tests: `tests/unit/test_r0100_t11_ai_slo_wiring.py`. No snapshot changed.
### Changed: quant errata folded into the corpus page; corpus re-pinned and retrieval re-measured (0.10.0 item Q8)

- **R-18.** `docs/quant/quant.md` now states the a-priori PROMOTE hurdle (3.0 for a search, 1.645
  for one preregistered test), the INCONCLUSIVE verdict with the daily Lo (2002) standard error and
  Q1's corrected worked value (0.447 for Sharpe 0.3 over 1,260 days), the research score of 25/30,
  a pointer to the regenerated power curve, and the near-constant-series tolerance. The errata page
  `docs/quant/quant-errata-0.9.0.md` is removed. Corpus pin: quant.md 0.4.0 to 0.5.0, corpus hash
  `ee8200cd3d90` to `2c4016d335b2`. BM25 retrieval re-measured (`python -m ceap.rag.evaluation`):
  chunking winner (section) and support gate (2.5) unchanged; train R@1 0.90 to 0.95, MRR 0.935 to
  0.960; held-out unchanged at 1.00; negative max support 2.06 to 2.08, no negative leaks.
  Snapshot change (needs the `snapshot-change` label): report goldens `research_R01`,
  `research_R03`, `tca_T01`, `tca_T05` (corpus hash, retrieved policy chunks, and the Q1 Sharpe
  interval). Not yet done: the dense and rerank caches in `data/rag/` need a rebuild with
  `fastembed` (`python -m ceap.rag.hybrid_eval --build`), after which `rag-hybrid-0.9.0` results are
  regenerated. Tests: `tests/unit/test_quant_errata_folded.py`.

### Added: CI replay gate for local-model results (0.10.0 item T3)

- **R-01.** `scripts/check_replay.py` runs in the CI lint job. For each committed per-seed result
  in `docs/evaluation/local_model/` it replays the matching cassette with no model running and fails
  when a request has no recording, when a recording is never asked for, or when any number in the
  committed file differs from the replay. Cassettes recorded before 0.10.0 have no manifest; the
  three 0.9.0 seed files are printed as "not gated (pre-0.10.0, awaiting re-record)" rather than
  passed, and any cassette with a manifest is gated, so a new recording cannot opt out. They become
  gated when T8 re-records them. Tests: `tests/repo/test_check_replay.py`.
### Fixed: the nightly can go green and its gates bite (0.10.0 item E2, findings R-25, E-13)

- **Why the nightly was red.** The `full` job ran the nightly sweeps under the coverage tracer and
  hit its 60-minute limit (cancelled 11, 12 and 14 November); on 13 November
  `test_cause_recall_200_seeds` passed its own 1800 s ceiling. The sweeps now run in a separate
  `sweeps` job (3.12, no tracer, 150 minutes) and the cause-recall sweep is one test per template,
  so xdist spreads it. `full` runs everything else in random order on 3.11, 3.12 and 3.13.
- **Why mutation testing never measured anything.** mutmut copies only `source_paths`; naming the
  two sub-packages left `mutants/src/ceap` without the rest of the package, the stats run could not
  import it, and the ratchet read the empty result as 0 survivors. The config now copies `src/`,
  mutates only `analytics` and `domain`, and copies `data/`, `docs/`, `config/`, `scripts/`.
- **Ratchet.** Fails when mutmut recorded no mutants, and on a null baseline after
  `null_allowed_until` (2026-11-30) in `tests/mutation/baseline.json`.
- **Baseline not measured.** A local run in WSL (8 workers) was still generating mutants when its
  30-minute cap ended, so the baseline is still null; the first nightly with this config must
  measure it before 30 November.
- **Hypothesis** is randomised in the nightly profile (`print_blob` for reproduction); PR stays
  derandomised.
- **Release gate.** `scripts/release_check.py` refuses a tag unless the last three completed
  nightly runs on main succeeded (a cancelled run counts as red).
- `ceap.llm.router.task_scope` no longer raises when an abandoned task's generator is finalised in
  another context (the unraisable warnings in the 13 November log).
- Tests: `tests/repo/test_e2_nightly_gates.py`. Three consecutive green nightlies need CI.

### Added: Python 3.13 declared and tested; 3.14 assessed (0.10.0 item E6, finding C-P2-2)

- `test (3.13)` joins the CI matrix and the 3.13 classifier joins `pyproject.toml`;
  `tests/repo/test_e6_python_versions.py` fails if the classifiers and the matrix differ.
- **Goldens.** One set, compared on every interpreter: no per-version golden files and no version
  branches in the golden code (checked by the same test), so a green matrix proves equality. It
  already holds across numpy 2.4.6 (3.11) and 2.5.3 (3.12 and later).
- **3.14.** The committed lock resolves for 3.14 with binary wheels only on Linux and Windows
  (resolver run, 2026-11-14), so no dependency blocks it; it is not in the matrix because it has
  not been run. `test (3.13)` has not run yet either: it needs CI. Note in
  `docs/testing/python-versions.md`.

### Fixed: the wheel and the image carry their data; installed-copy smoke check (0.10.0 item E3, finding R-27)

- **One resolver.** `ceap.paths.data_root()` replaces the module constants that resolved `data/`
  from the source tree (`Path(__file__).parents[3] / "data"`), which pointed nowhere once
  installed. Order: `CEAP_DATA_ROOT`, then the copy inside the wheel (`ceap/_data`), then `data/`
  in a checkout.
- **The data ships.** `setup.py` (a build hook only; metadata stays in `pyproject.toml`) copies the
  runtime files listed in `ceap.paths.PACKAGED` into the wheel, including `docs/quant/quant.md`,
  which the knowledge manifest lists outside `data/`, so the installed corpus hash equals the
  checkout's. The image now gets its data from the installed package; the separate `/app/data`
  copy is gone.
- **Loud failure.** A missing default knowledge corpus or model store raises `DataFileMissing`
  (not an `OSError`, so the detector and calibration fallbacks cannot swallow it). Before, the
  knowledge base came up empty and the change detector and calibration ran unfitted.
- **Smoke check.** `python -m ceap.smoke` runs one investigation and fails unless every corpus
  document, the change detector and the calibration model loaded. CI runs it from an installed
  wheel outside the checkout (new `wheel-smoke` job) and inside the image (`docker` job). Checked
  locally against an installed wheel (10 documents, both model hashes); the image was not built
  locally (no Docker on this machine). Tests: `tests/unit/test_e3_data_root.py`.

### Changed: pre-trade model card: challengers, regime-level conformal, functional-form test (0.10.0 item X4, finding R-24)

- The card shows MAE and bias against a constant and a spread-only challenger; the model's MAE is
  close to theirs (price risk dominates) and its gain is lower bias where impact is large.
- **Interval repair.** The conformal quantile is now calibrated per regime (two-layer conformal,
  Dunn, Wasserman and Ramdas 2022) over the five fit families plus five stressed variants of T01
  (each generator axis doubled, depth halved; new `UniverseConfig.regime`). q rises from 1.653 to
  1.714. Coverage is tested with a Wilson interval at the Kish effective sample size (session
  clusters, design effect about 2.1); the script exits 1 when a shifted family's interval excludes
  nominal 90 %.
- Result: T02, T04, T06 and T10 now contain nominal (T10 was outside); **T08 still fails**
  (87.1 %, interval 84.5-89.3 %): an order-size multiplier of ten is outside the calibration
  regimes. Kept open and marked by a strict xfail test; not tuned on the test families.
- **Functional-form shift.** New `UniverseConfig.impact_law="linear"` (equal to the square-root law
  at 1 % ADV). On the five shifted families under it, without refitting, coverage is 88.6-90.8 %
  except T08 at 52.4 %, where the model under-predicts cost by 18.6 bps.
- Snapshot change: `src/ceap/ml/pretrade_model.json` (new q, evaluation tables); the pre-trade
  interval bounds in the TCA goldens (`tests/golden/tca_T01.json`, `tca_T05.json`) move with q.
### Added: research evaluation on the 500-name universe (0.10.0 item Q6, finding R-30)

- `evaluation_platform(universe)` builds the research datasets on `core30` (default) or
  `synthetic500`; `scripts/research_eval_universes.py` runs the 30 scenarios on both (in parallel
  slices, each on a fresh platform and in-memory ledger), refuses to publish unless every scenario
  completes, and writes `capability.research_500` into `latest.json` (the report builder does the same).
- **Results.** 30 names: 25/30 verdicts match the truth (0.8333, unchanged). 500 names: 21/30 agree
  with the 30-name truth (0.70; clear scenarios 0.875); verdicts 14 PROMOTE, 16 REJECT. Nine verdicts
  move, all to PROMOTE: RS02, RS22, RS24 (truth PROMOTE or INCONCLUSIVE) and RS07, RS08, RS09, RS19,
  RS20, RS21 (30-name truth REJECT). The ground truth is derived for 30 names, so the 500-name figure
  is agreement, not accuracy; deriving truth for 500 names is not done. The 500-name run needs
  `CEAP_STEP_TIMEOUT_SECONDS` well above the 30 s default (1800 was used; about 140 s a scenario).
- README corrected: the scenarios run on the 30-name universe and are re-run on 500 names.
- Tests: `tests/unit/test_q6_universe_eval.py`.

### Added: research realism toolkit wired into the verdict (0.10.0 item Q4, findings R-17, R-20)

- **Factor-neutral IC is a gate condition.** `evaluate_signal` now reports
  `ic_t_stat_factor_neutral`: the gate's non-overlapping IC with the signal ranks and the forward return
  both residualised, date by date, on each name's trailing five-year market beta
  (`risk_research.rolling_betas`, ex-self market; `risk_research.factor_neutral_ic`, which calls
  `residualise`). `NO_ALPHA` is raised when it is below the hurdle, with its own rationale and
  hypothesis row. A one-year beta was tried first and left a 2400 bps beta premium at IC t 5-7.
- **Market-beta family through the full verdict** (`adversarial.full_verdict_study`, 16 seeds a point,
  `docs/research/adversarial_full_verdict.json`): PROMOTE 0, 0, 0 and **2 of 16** at 0, 600, 1100 and
  2400 bps; re-scored without the new statistic, 0, 0, 1 and 16 of 16. The drift baseline keeps
  14 of 16 at 2400 bps (unchanged). The two that still pass are the limit of residualising on an
  estimated beta.
- **PBO and effective trial count on the 24-trial matrix.** The backtest tool's trial record carries
  `search` (`ceap.analytics.search_diagnostics`: PBO by CSCV with 16 blocks, effective trials by the
  eigenvalue participation ratio and by clusters at 0.7) and writes `n_trials_effective`, which the
  deflated Sharpe reads (floored at 24, so it cannot relax the gate). Cached per dataset and window;
  the tool's description and schema are unchanged, so the catalogue hash and cassettes do not move.
  `pbo_cscv` is now vectorised (12,870 splits in about 0.3 s; equal to the per-split definition).
- **Ledoit-Wolf predicted volatility in the risk step.** The as-of target portfolio reports
  `predicted_vol_ann` and `covariance_shrinkage` from the held names' trailing 252-day returns.
- All of these are fields of the assessment's metrics.
- **What moved.** The 30 research scenarios and 18 golden research items were re-run: **no verdict
  and no flag moved** relative to Q2 (scenarios 25/30). **Snapshot change:** `tests/golden/research_R01.json`
  and `research_R03.json` regenerated; the diff only adds fields (the new metrics, the factor-neutral
  hypothesis row, the trial `search` block, and the Q2 ledger fields `sharpe_basis` and
  `sharpe_variance_net`, which the Q2 commit had left out of these two files).
- Tests: `tests/unit/analytics/test_q4_realism_wiring.py`.

### Fixed: model registry checks staleness and drift at load; trial ledger persistent (0.10.0 item Q7, findings Q-10, Q-12)

- **Stale models fail a test.** All three registered models had been fitted to generator
  `ce0da55d75b78559` and the model-card header named a third hash, `d3074158ce168b03`; the generator
  is now `7f59073801a673bc`. `ModelRegistry.load` emits a `StaleModelWarning` when a card's generator
  hash differs from the current one, and `tests/unit/ml/test_q7_registry.py` fails for any stored model
  in that state and for a card header that names any other hash.
- **Retrained.** `python scripts/train_ml_models.py` refitted the change detector, the change-point
  calibration and the ridge combination. **No parameter and no metric moved**: the three model hashes
  are unchanged (`b2a6c3822c3e4459`, `29d16b7ea00c171f`, `1b5b2fcd6870c289`); only the card provenance
  (generator hash, label, date, the 0.10 inventory fields, and the training-data hash of the two
  engineering models, which embeds the generator hash) changed, in
  `data/models/*.json` and `docs/ml/model_cards.md`.
- **Drift checked at load.** `ModelRegistry.load(name, features=...)` runs the PSI drift check and keeps
  the values on `RegisteredModel.drift`. The engineering change-point scan now calls
  `anomaly.detector_drift` on the live reference window before detecting, so the check runs in
  production rather than only in tests (finding R-17 listed `check_drift` as uncalled).
- **Trial ledger persistent by default.** Without `CEAP_TRIAL_LEDGER` the ledger was per process, so
  the trial count reset at every restart and undercounted the search. It now appends to
  `var/trial_ledger.jsonl` by default; `CEAP_TRIAL_LEDGER=:memory:` opts out. The test suite, the
  scenario evaluation (`evaluation_platform`, beside case memory being off) and model training use an
  in-memory ledger so one run cannot change another's verdict. `Platform` and
  `build_in_process_client` take an optional `trial_ledger`.
- Tests: `tests/unit/ml/test_q7_registry.py`.

### Fixed: deflated Sharpe gate floored, tested on the production path, bar 0.95 (0.10.0 item Q2, finding R-16, decision O5)

- **Variance floored at `1 / T`.** The trial ledger's cross-trial variance of the per-day Sharpe
  replaced the null value `1 / T` whenever it was positive, so a ledger of near-identical trials
  shrank the expected maximum Sharpe. The reviewer's case (Sharpe 0.3, ten years, 24 trials, ledger
  variance 1e-8) scored 0.83; it now scores 0.15, as with the null variance. Under the null each
  trial's per-day Sharpe has sampling variance about `1 / T` (Lo 2002), so a smaller spread means
  correlated trials, not a lower bar.
- **One production call.** `research_assessment.alpha_deflated_sharpe` is used by the verdict and
  by the null sweep, which used to call the formula with its own arguments. The sweep passes no
  ledger variance, which is the floor itself, so its rate bounds the shipped gate from above.
- **Gross and net recorded consistently.** The backtest tool wrote the *net* in-sample Sharpe to the
  ledger while the gate deflates the *gross* one. Records now carry the gross per-day Sharpe
  (`sharpe_basis: "gross"`) with the net beside it (`sharpe_daily_net`, `sharpe_variance_net`);
  pre-0.10 records, whose basis is unknown, are counted as trials but left out of the variance.
- **Bar 0.5 -> 0.95** (one-sided 5 %, Bailey and Lopez de Prado 2014). **False-PROMOTE rate at the
  new bar:** the 500-seed null sweep over the full 24-configuration search found 0 of 500 at every
  grid hurdle, t >= 0 included (95 % Clopper-Pearson upper bound 0.74 %); the old bar gave 17 of 500
  at t >= 0, 5 of 500 at 1.645 and 0 of 500 at 3.0. New sweep hash `8d1dc8bd91e4...`
  (`data/reference/research_thresholds.json`, `docs/research/null_sweep_per_seed.json`, one line
  appended to `data/reference/sweep_ledger.jsonl`).
- **What moved.** The 30 research scenarios and the 18 research items of both golden sets were run
  before and after on the same code base. **No verdict moved** (scenarios 25/30; golden verdicts
  13/15 and 2/3). Flags moved in five rows: RS23 (`G-RES11-12`) `COST_DRAG` -> `NO_ALPHA` (DSR
  0.93), RS25 (`G-RES11-13`) and RS26 `COST_DRAG` -> `NO_ALPHA` (DSR 0.82 and 0.81). Scenario flag
  coverage goes from 25/30 to 24/30 (RS23 now carries its expected `NO_ALPHA`; the two reversal
  scenarios lose their expected `COST_DRAG` because the alpha chain stops at `NO_ALPHA` first). The
  golden must-flag counts are unchanged (13/15, 1/3: `G-RES11-12` gains, `G-RES11-13` loses). No
  golden file and no ground truth was edited.
- Tests: `tests/unit/analytics/test_q2_deflated_sharpe.py`.

### Fixed: redaction kept quantities; key errors never echo key material (0.10.0 item E7)

- **E-09.** The redactor removed every 8 to 19 digit number, deleting quantities, notionals and
  order ids from the user's question. A bare long number now survives ("Buy 12000000 shares"); it
  is redacted only after an account word (account, acct, card, a/c) or in card-style groups.
- **E-08.** A malformed `CEAP_API_KEYS` entry no longer appears in the error message.
- Tests: `tests/unit/test_redact_e7.py`.
### Added: per-package branch coverage gate (0.10.0 item E1, finding C-P2-1)

- `[tool.coverage]` in `pyproject.toml` (branch, source `ceap`, parallel for xdist).
  `scripts/coverage_by_package.py` reads the JSON report, prints a per-package table and fails
  when a package is under its floor (90 %, 95 % for `analytics` and `domain`) and under its
  recorded baseline. The 3.12 test job now writes `coverage.json` and runs it.
- **Measured, not met.** On the unit tier, 11 of 19 packages are under the floor (lowest:
  `studies` 44.6 %, `api` 55.0 %, top-level modules 68.7 %). The table is in
  `docs/testing/coverage-by-package.md`; those packages are held at their measured figure as a
  ratchet until behaviour tests raise them. No coverage was added in this item.

### Added: behaviour tests raise per-package coverage (0.10.0 item E1, lane W2)

- Unit-tier tests for the study E1 runner (synthetic Ken French files, crash-rerun and refused
  second opening, ETF gate with an in-memory `yfinance`), the HTTP gateway (every task state,
  ownership and kind scoping, cancel, idempotency, rate limit, body limit, prewarm, key hashes),
  fail-closed configuration, golden-set scoring, the AI SLO monitor and Prometheus exposition, MCP
  grant signing and client reconnect rules, and the ingestion-screen measurement.
- Unit tier, before -> after: `studies` 44.6 -> 99.7 %, `api` 55.0 -> 91.6 %, top-level modules
  68.7 -> 79.3 %, `observability` 78.0 -> 96.5 %, `mcp` 73.8 -> 84.4 %, `eval` 72.4 -> 73.1 %. The
  ratchet in `scripts/coverage_by_package.py` is tightened to the new figures; eight packages remain
  under their floor. `tests/test_industry_momentum.py` moved to `tests/unit/`. No source changed.
- Lane W9: unit-tier behaviour tests for the knowledge-corpus pin check, the injection corpus and
  its runner, the B3 sensitivity check (every model metric moves under its degraded client), the T5
  claims-audit replay, the R-01 fallback refusal, the drift monitors on synthetic cassettes, model
  pins and prompt integrity, untrusted data blocks and context truncation, cassette match keys and
  the publish gate, and the harness evidence-binding rules. Unit tier, before -> after: `eval`
  73.1 -> 91.3 % (now meets the floor), top-level modules 79.3 -> 85.6 %, `llm` 74.8 -> 81.2 %,
  `harness` 78.2 -> 80.0 %. Ratchet tightened; `analytics` and `paper` baselines kept (four
  analytics tests fail on the base branch). No source changed; no bug found.

### Fixed: deployment recipes (0.10.0 item E4, finding R-26)

- **Pre-hashed keys alone now start the server.** `validate_for_serving` refused any configuration
  without raw `CEAP_API_KEYS`, so the shipped compose example, which sets only
  `CEAP_API_KEY_HASHES`, could not start. Hashed keys now count, and they are parsed at start-up so
  a malformed entry fails there rather than on the first request. No keys at all is still refused.
- **The compose stack is started in CI.** A new `compose` job runs `scripts/compose_smoke.sh`: it
  generates a key, hashes it with the new standard-library `scripts/hash_api_key.py` (key read
  from the environment, never the command line), brings the stack up behind Caddy, and checks
  `/health`, a 401 without a key and a 200 from `/tools` with it.
- **Cookbook recipes corrected.** Recipe 26 used an 8-character raw key that the 256-bit entropy
  rule refuses; it now uses a hashed key. Recipe 6 listed and decided approvals with the admin key,
  whose role lacks `approvals:decide`; it now uses the approver key. Tests in
  `tests/repo/test_e4_deploy_recipes.py` check each recipe against the code.
### Fixed: one Sharpe standard error, Lo (2002) daily form (0.10.0 item Q1, finding R-15)

- The Sharpe interval that separates INCONCLUSIVE from REJECT used `sqrt((1 + SR_ann^2 / 2) / years)`.
  In Lo (2002, eq. 9) the squared term is the per-period Sharpe, so with daily data the annual Sharpe
  squared must be divided by 252. The old interval was about 22 % too wide at Sharpe 1 and 73 % at
  Sharpe 2 (one year of data). There is now one function,
  `ceap.analytics.numerics.sharpe_standard_error`, used by `research_assessment.sharpe_interval`,
  `profitability` and `paper.forward.sharpe_ci`; the wrong copy is gone. A simulation test checks
  that the 95 % interval covers the true Sharpe in 93-97 % of 2,000 draws.
- **Verdicts re-run.** The 30 research scenarios and every research item of both golden sets
  (`golden_set.json`, `golden_set_research_v1_1.json`; 48 rows in all) were assessed with the old and
  the new interval on the same inputs. **No verdict moved** (0 of 48); the scenario score stays 25/30.
  Example: RS01's full-period interval narrows from [0.50, 1.63] to [0.61, 1.51]. No golden or
  snapshot file changed.
- The 0.9.0 errata repeated the wrong value: for Sharpe 0.3 over five years the standard error is
  0.447, not 0.457, and the interval is [-0.58, 1.18]. Corrected in `docs/quant/quant-errata-0.9.0.md`
  (`quant.md` itself is corrected with item Q8).

### Changed: power curve at the hurdles in force (0.10.0 item Q5, finding R-19)

- `docs/quant_power_curve.json` was computed at the retired hurdle 0.40. It is regenerated by
  simulation (40 seeds a point, `python -m ceap.analytics.power`) at both hurdles the gate uses: 1.645
  for one preregistered test and 3.0 for a result mined from a search. With 80 % power the IC gate
  detects a planted premium of about 1500 bps a year (expected gross Sharpe 0.75) at 1.645 and about
  1800 bps (gross Sharpe 1.04) at 3.0, on the 30-name synthetic universe. A gross Sharpe of 0.5 is
  detected 18 % of the time at 3.0. The table and the plain statement are in `docs/research/README.md`.

### Fixed: one zero-variance tolerance in five regressions (0.10.0 item Q3, finding Q-14)

- Five places tested a sum of squares or a standard deviation for exactly zero, so a constant input
  carrying floating-point noise (a sum of squares near 1e-30) produced an R-squared or a beta of
  arbitrary size. The review found three more sites than the 0.9.0 errata disclosed. All five
  (`risk_research.factor_regression`, `portfolio_risk.raw_betas_ex_self`,
  `profitability.factor_alpha`, `ml_signals._moments`, `attribution._ridge_contributions`) now call
  `ceap.analytics.numerics.negligible_variation`: a series counts as constant when
  `sum((y - mean)^2) <= n * max((1e-9 * max|y|)^2, (1e-12)^2)`. The scale is `max|y|`, not the mean,
  so a zero-mean series is handled; the absolute floor covers an all-zero series.
- A constant dependent series gives R-squared NaN with an `r2_note`; a near-constant market leaves the
  beta unestimated (standard error infinite, so shrinkage takes the target); `factor_alpha` returns NaN
  with a `note`. Regressions on varying data are unchanged.

### Added: durable runs resume through the product (0.10.0 item H1, findings R-11 and AI-2-05)

- **Entry point.** `POST /tasks/{id}/resume` and `ceap resume <id>` continue an `INTERRUPTED` or
  `FAILED` task under its own id. Before, nothing in the product re-ran a task id; the 0.9.0 chaos test
  replaced the id by hand. The owner or an admin may resume; the run uses the principal and roles it
  was first submitted with, which are now journalled. A finished task, a running one, or one whose
  first attempt ran without `CEAP_DURABLE_RUNS=1` is refused with 409.
- **Model replies are journalled.** A resumed run used to call the planner and every model-backed
  agent again, so a model that answers differently could return another plan (AI-2-05). Each reply is
  now journalled by agent, step and call order and read back on resume. With the journal on, ids are
  minted from the task id and the scope, so the finding and evidence ids those replies cite are the
  same in the resumed run. Runs without the journal are unchanged.
- **The approval match ignores run-minted ids.** The journal keyed a decision on the approval's full
  content hash, which includes fresh finding and evidence ids, so a resume asked the approver again
  and wrote a second case. It now keys on `stable_content_hash`, which leaves those ids out. The
  approver still signs the full content hash (A14).
- Test: `tests/unit/test_r0100_h1_resume.py` kills a signed-off run with `os._exit` straight after the
  sign-off is journalled, resumes it through the API by id alone, and finds the same number of model
  and tool calls as an uninterrupted run, one approval request and one case. With model journalling
  disabled the same test fails (6 model calls against 4). `test_r090_durable.py` and
  `test_r090_chaos.py` now count tool rows only, since the journal also holds model rows.

### Fixed: case memory holds human decisions only; waiting for a human is not task time (0.10.0 item H6, findings AI-2-06, AI-2-07, AI-2-08)

- **Human decisions only (AI-2-06).** An auto-approval or a timeout was stored as a case, and the
  planner was told these were human precedents: a dev-mode auto-approval, or a REJECTED that was
  really a 120-second timeout, carried the weight of a reviewed decision. The harness now records no
  case when the decider is a system actor (`timeout`, `policy`, `auto-approver`), and
  `CaseMemory.append` refuses one as well.
- **Owner boundary and expiry (AI-2-07).** Cases were partitioned by dataset only, so one trader's
  question and an approver's comment reached the next principal's planner prompt. A case now records
  its owner (the task's requester) and is read back only for that owner, or for a principal with
  `tasks:read-any`. A case older than 90 days (`ttl_days`) is not read and is pruned on the next
  write; a full partition is still refused, now with a `case_memory.full` audit entry. The case id is
  derived from the task and the step, so a resumed run does not write a second case. The store is
  still per process (a database table remains a follow-up).
- **Human wait does not count (AI-2-08).** An approval given after the task deadline was followed by
  `TimeoutError("task deadline exceeded")`: the task failed after a person approved it. The task clock
  (`RunContext.clock`) now stops while an approval is pending, and every deadline check reads
  `RunContext.due()`, the deadline moved on by the time spent waiting. Concurrent approvals count
  once. An agent's own overall timeout is still fixed when the agent starts.
- Tests: `tests/agent/test_r0100_h6_case_memory.py`. An auto-approved and a timed-out sign-off leave
  memory empty, a person's sign-off writes one case; an approver who takes ten minutes on a task with
  a 300 s budget no longer times the task out (with the clock fix removed the same test fails with
  "task deadline exceeded"); another principal does not see a case and a 91-day-old case is not read.
  Existing tests updated: the 3B.7 recall test signs off with a person, the 3B.7 bounds test and the
  case-memory injection surface test give their planted cases a current time and an owner.

### Fixed: two baselines, two names (0.10.0 item H5, finding R-03)

- One report gave two values for the same quantity under the same word. T06 at 0.9.0: the summary
  said "implementation shortfall delta -3.7 bps" and observation 5 said shortfall "decreased by
  28.6 bps relative to the baseline's +13.9 bps". The attribution measures against the pooled matched
  peer windows; the execution agent measures against the task's baseline window, the hour before.
  Each number was right, and both were "the baseline".
- The two now have names, `ceap.analytics.attribution.baseline_name`: "matched peer windows" and
  "prior window". The mock narrative's summary names the attribution's baseline; the execution
  observations, the EXECUTION and MARKET CONDITIONS sections and the latency line name the prior
  window; the quant agent's delta finding names its own. Findings that carry a delta also carry a
  `baseline` attribute.
- The critic has a new deterministic check, `critic_checks.baseline_conflicts`: two deltas of one
  metric (from findings or the attribution) under the same baseline name must agree to 0.05 bps, or
  the critic records a contradiction prefixed `baseline_conflict`.
- Scope: the attribution's own rationale strings ("latency ratio vs baseline = 10.10") still say
  "baseline"; they carry ratios, not a second delta of the same metric, and renaming them would
  change every attribution record. The README example (already stale before this change: its
  numbers are from an older run) is left for the documentation pass.
- Test: `tests/agent/test_r0100_h5_named_baselines.py` runs T06 and finds the named baselines in the
  summary and the observations and no delta line under "the baseline"; a planted finding that prints
  the execution delta under the attribution's name makes the check fire.
- Regenerated (wording only, reviewed): `tests/golden/tca_T01.json`, `tests/golden/tca_T05.json` and
  the ten hand-built cassettes `tests/cassettes/investigation_T01.json` to `_T10.json` (the critic
  request carries the finding statements, so its hash moved), and the 3B.8 approval review snapshot
  `tests/agent/snapshots/approval_review_T10.json`.

### Fixed: a citation must come from a chunk that supports the claim (0.10.0 item H4, finding R-09)

- The citation audit proved that the quoted text is a real chunk, and that the citation is attached to
  the claim's sentence; it never compared the chunk with the claim, so a real quote about venue
  configuration could back a claim about escalation thresholds. `claim_citations` now also requires
  `citation_supports`: every number in the claim occurs in the cited chunk, and the chunk shares at
  least two of the claim's key terms (all of them when it has fewer) and at least half of them.
- This is a lexical check, not entailment: a chunk that uses the claim's words to say the opposite
  still passes. An NLI scorer is left for when a real corpus exists.
- The local-model results page now shows the citation row with n = 0 and the reason: the Ollama
  adapter returns no citation blocks, so no citation was ever verified against a real model.
- Test: `tests/agent/test_r0100_h4_citation_support.py`, ten planted right-chunk and wrong-chunk pairs
  over the shipped knowledge base: 0 of 10 wrong-chunk citations pass, 10 of 10 right-chunk citations
  are kept, and a claim whose number the chunk does not state fails. The A7 fixture in
  `tests/agent/test_r080_narrative_guardrails.py` now carries chunk text, since a citation with no
  passage can no longer support a claim. No golden or snapshot changed.

### Fixed: standalone MCP servers no longer stage on capability alone (0.10.0 item H2, finding R-12)

- **A signed grant for every HIGH-risk call.** A server started on its own (`python -m
  ceap.mcp.execution`) adds an `approval_grant` argument to each HIGH-risk tool and refuses the call
  unless it is an HMAC-SHA256, keyed with `CEAP_MCP_GRANT_KEY`, over the tool id and the exact
  arguments (`MCPServerDefinition.sign_grant`). With the key unset every HIGH-risk call is refused.
  Before, the `admin` role alone was enough to stage paper orders with no approval and no four-eyes.
- **The portfolio hash is required.** `stage_orders` refuses a call without `approved_portfolio_hash`.
  It stays optional in the schema because the harness binds it after the approval gate, which is
  later than plan validation reads the schema. The in-process harness path is unchanged.
- Test: `tests/contract/test_r0100_h2_stdio_grant.py` drives the server over stdio with the `admin`
  role: no grant and a grant signed with the wrong key are refused; a valid grant gets through.

### Fixed: side-effecting tool calls are attempted once and carry the idempotency key (0.10.0 item H7, finding AI-2-04)

- The executor no longer retries a tool whose metadata says `read_only=False`. A timeout or transport
  error after the write used to repeat it up to three times. Read-only tools keep their retries.
- `ToolExecutionContext.idempotency_key` now carries the run's key for the call
  (`ceap.harness.durable.idempotency_key`), so a side-effecting tool can make a repeat a no-op, for
  example after a resume.
- Test: `tests/unit/test_r0100_h7_idempotent_side_effects.py` injects a failure after the write of a
  `stage_orders` double and finds one staged set and the run's key on the call.

### Fixed: the harness cannot finalise without a critique and an evidence check (0.10.0 item H8, finding AI-2-11)

- `StateMachine` refuses FINALISING unless the critic has run since the last agent output and the
  evidence has been validated since that critique. The engine records both, and a FINALISE step that
  comes without a prior validation step now validates the evidence itself; before, a plan whose
  critic had already run went from EXECUTING to FINALISING with no evidence check.
- The step dispatch has an `else`: an unknown step type fails the run instead of being skipped.
- Test: a Hypothesis property (`tests/properties/test_r0100_h8_state_machine_guards.py`) drives random
  transition sequences and finds no path to FINALISING without both flags.

### Fixed: the ingestion injection screen normalises first, and is measured (0.10.0 item H3, finding R-08)

- `screen_document` now applies the same NFKC and zero-width stripping as the prompt path (both use
  `ceap.util.unicode_clean.clean_untrusted`), so one zero-width character no longer bypasses it.
- Recall and false-positive rate are measured on every CI run and committed in
  `docs/evaluation/ingestion_screen.json`. On the 50 base strings of `injection_corpus_v1` the screen
  flags 10 (recall 0.20, 95 % Wilson interval 0.11 to 0.33), the same with zero-width characters
  inserted; it flags none of the 10 knowledge documents or their 127 paragraphs (after the Q8 errata fold). The patterns were
  not tuned to the corpus, so 0.20 is an honest and a low figure: the screen is a tripwire, and the
  defence remains the nonce-tagged data blocks on the prompt path.
### Added: local model digests pinned and checked; cassette manifest (0.10.0 item T7, findings R-13, AI-1-10)

- **Digest pinned, not only the tag.** `model_pins.toml` gains `[local_models]` with the Ollama
  content digest of each local model (the digests recorded in the 0.9.0 cassettes). The live
  `OllamaLLMClient` reads `/api/tags` at start and raises `ModelDigestError` when the server's digest
  is empty or differs from the pin. Before, the digest was fetched for provenance only, came back
  empty on any error and was never compared.
- **Every new interaction carries its manifest:** the request text, the prompt id and version, the
  source commit and the Ollama version (from `/api/version`). Cassettes recorded before 0.10.0 load
  unchanged with these fields empty.

### Fixed: the planner validator agrees with the sanitiser on `None` (0.10.0 item T4, finding R-04)

- **A null optional argument is "not given".** `plan_semantic_errors` now skips `None` arguments,
  as `_sanitise_arguments` already did. Before, `"dataset": null` was compared as the string
  `"None"` with the pinned dataset and the plan was rejected; on replay 72 of 73 validation errors
  were this one rule. Re-scoring the recorded 0.9.0 planner replies gives at least 27 of 30 valid at
  the first attempt (0.9.0 published 15 of 30); the published result files are not rewritten here.
- **Errors are kept.** The planner output carries `validation_errors` (the first attempt's list)
  and each planner-eval result row stores it, so a reader can see why a plan needed a repair.
- **Not done in this item:** removing `null` from optional schema members and making windows an
  enum and dataset and symbol constants. Each changes the schema sent with the request, and so the
  cassette match key; it waits for the re-recording (T8/T9).

### Fixed: local-model evaluation runs use no fallback (0.10.0 item T1, finding R-01)

- **A cassette miss is now an error.** `ceap.eval.local_model.run_seed` builds its `LLMRouter`
  with no fallback client, and `run` raises `EvaluationFallbackError` when a seed's run hits a
  request with no recording. `LocalCassetteClient` counts its misses, so a miss is still caught
  when a caller downstream swallowed the error and carried on.
- **No `local_model` label on fallback answers.** `run` refuses to return
  `evidence_class: local_model` when any call in the run missed the cassette or was answered by a
  fallback client. Replaying the 0.9.0 cassettes through the golden path now fails by name instead
  of reproducing the mock's numbers.
### Changed: pre-trade model card reports point error and a coverage test (0.10.0 item X4, finding R-24, partial)

- The card now shows MAE and bias against a constant and a spread-only challenger, and coverage per
  shifted family with a Wilson 95 % interval. Result: the model's MAE is within a few hundredths of a
  bps of the challengers; its gain is lower bias where impact is large (T08, T10).
- Coverage is significantly below nominal 90 % in T08 (85.9 %) and T10 (88.3 %); the training script
  now exits 1 when an interval excludes nominal. **Not fixed yet**: the interval repair and a test on a
  different functional form remain open for this release.

### Added: forward-log tamper evidence and staleness check (0.10.0 item X2, finding R-22)

- Each paper-daily run verifies the previous day's anchor before appending, runs the replay gate
  (`ceap paper gate`), writes `anchor.json` and puts its digest in the commit message. A rewritten
  or truncated history fails the next run.
- New `ceap paper staleness` and a weekday 15:00 UTC job: any NYSE session before today with no row
  opens a `paper-stale` issue (one miss is enough). Tests: `tests/unit/paper/test_staleness_x2.py`.
- Wording: the log is append-only by git history, not by key, while the published development key
  is used. Checked on the real `forward-log-v2` (2 sessions): gate passes, no session missed.

### Fixed: execution library labelled truthfully; POV band and completion (0.10.0 item X5)

- **R-23.** `ceap.execution` is now described everywhere as a *simulator library, not used by the
  default paper book*; the paper book imports only the auction model and the LOB cost measurement,
  behind opt-in settings. `tests/repo/test_execution_label.py` keeps the label in place. Wiring a
  scheduler and the trading rules into the paper path is 0.11.0.
- **X-07.** The POV band did nothing: the target `clip(rate * V, lo, hi)` is always inside the band.
  POV now follows its plan and the band clips it.
- **X-08.** Schedulers left the parent unfinished and the last slice crossed at its start. The last
  slice is now worked like the others, and a completion step shortly before the end cancels what
  rests and takes the remainder with a market order. A parent completes in all 40 seeds tested.

### Fixed: schema rollback could re-seal a forged audit chain (0.10.0 item X3, finding R-21)

- Setting `PRAGMA user_version` below v4 made every audit row count as legacy, and the next normal
  open re-ran migration 0004 and sealed the forged rows with the real key. Migration now refuses a
  file that already has a `chain_epoch`, a `schema_seal` or any keyed row.
- New migration **0006** stores the highest schema version reached, sealed with the chain key;
  `verify_chain()` fails when `user_version` is below it or the seal does not verify.
- Tests: `tests/unit/test_schema_rollback_r21.py` (the attack end to end, plus an edited seal).

### Fixed: four known issues from 0.9.0 (0.10.0 item X6)

- **Lot larger than the session cap.** A paper order whose single lot exceeds the per-session ADV cap
  is now cancelled at once (`reason="lot_above_cap"`) instead of sitting unfilled until its TTL.
- **Zero sigma band.** A name whose window prices never moved gets the band cap, not a zero band
  that blocked every move.
- **Andrews lag on constant residuals.** `industry_momentum.andrews_lag` returns 0 instead of
  dividing by zero.
- **Idempotency key for non-JSON arguments.** Sets, dataclasses, bytes, dates, enums, decimals,
  paths and pydantic models now have a canonical form; any other type raises `TypeError` rather
  than hashing a memory address. All current callers pass JSON-native arguments, so no key changes.
- One test each (`tests/unit/test_known_issues_090.py`, `tests/unit/paper/test_r090_l4_paper.py`).

### Fixed: the paper forward log (0.10.0 item X1, finding R-02)

- **Why the scheduled job wrote nothing on 10 and 11 November.** Both scheduled runs failed with
  `fatal: invalid reference: origin/forward-log`: `actions/checkout` fetches only the triggering
  ref, so once the branch existed the job could not check it out. The job now fetches the branch
  first. The repository variable was not the cause.
- **No session before its close.** `PaperJob.run_day` refuses a session whose 16:00 New York close
  (13:00 on early-close days) has not passed. The 10 November row was written at 03:42 UTC, about
  eleven hours before that session opened.
- **Forward record restarted (decision O4).** `forward_start` moves from 2026-11-09 to 2026-11-12
  and the job writes to a new branch, `forward-log-v2`. Branch `forward-log` is kept unchanged as a
  disclosed false start (rows for 9 and 10 November, the second written early). The feed is
  synthetic, so the early row leaked no market information.

### Corrections to the 0.9.0 notes (docs only, found by the 2026-11-12 review)

- **The local-model "golden slice" was not measured.** 0.9.0 published primary cause 6/9,
  must-mention 9/9, no forbidden claim 6/9 and verdict 3/3 as results for `qwen2.5:7b-instruct`.
  They are output of the deterministic mock. The recorded cassettes hold no call from the golden
  run, so on replay every request missed the cassette and `LLMRouter` answered from its
  `MockLLMClient` fallback (`src/ceap/eval/local_model.py:94`, `src/ceap/llm/router.py:404-417`).
  A replay of seed 1 with a counter gave 0 cassette hits and 15 mock answers and reproduced the
  published scores. The live runs scored 0/3 on primary cause in each seed, with the backtest tools
  timing out under the model's CPU load, so they are not a measurement of the model either. The rows
  are withdrawn from the README, this file, `docs/evaluation/local_model/README.md`,
  `docs/evaluation/evaluation.md`, `docs/ai-agents/ai-agents.md` and `docs/learn/ai/06-evaluating-ai.md`.
  The planner, reporter and critic numbers come from recorded model replies and are unchanged. The
  committed result files still carry the golden block and are regenerated with the 0.10.0 re-run.
- **The paper forward log has not run daily.** The `forward-log` branch holds one manual run, made
  at 03:42 UTC on 2026-11-10, with rows for 2026-11-09 and 2026-11-10; the second row was written
  before that session opened, and no scheduled run has been recorded since (checked 2026-11-12). The
  README and the site said it "runs daily". They now say what is on the branch. A date guard and a
  clean restart of the record are planned for 0.10.0.

## [0.9.0] - 2026-11-12

The last planned release. It was built as six parallel lanes (plan and per-step status:
`docs/review/2026-11-09/PLAN.md`) and integrated on one branch. Evidence class, unless a line says
otherwise: synthetic data and the deterministic mock LLM. The two research studies use free real
data; the local-model numbers come from one CPU machine.

### Highlights

- **A real model, measured locally.** `ceap.llm.ollama.OllamaLLMClient` runs a pinned open-weight
  model (`qwen2.5:7b-instruct`, challenger `llama3.1:8b-instruct-q4_K_M`) behind `LLMClient`; runs
  are recorded as cassettes and replayed in CI. Results: qwen2.5:7b on one laptop, pooled over 3 seeds from replay: plan valid 15/30 (0.50), tool precision 1.00, recall 0.73; critic 35/45 (0.78); 0/39 untraceable numbers reached a report; end-to-end golden slice not measured (the 6/9 and 3/3 first published were mock-fallback output, withdrawn 2026-11-12); injection obeyed 3/40 (all via the question); judge enables only `hedged` (kappa 1.00); challenger not measured (known issue)
  (`docs/evaluation/local_model/README.md`). No hosted model was called.
- **Critic statement checks.** `contradicted`, `numeric_mismatch` and `direction_mismatch`
  (`ceap.agents.critic_checks`): planted-fault detection 6/15 -> 15/15 on the existing and a
  held-out set, 0/10 false alarms (`python -m ceap.agents.critic_planted`; synthetic).
- **E1, real data: NULL.** Preregistered 49-industry momentum on Ken French data; holdout 1990-2015
  opened once, net 10 bps NW t 1.16 < 1.645 (`docs/research/e1/`).
- **Hybrid retrieval, opt-in.** Reranking beat BM25 on R@3 (+0.065, CI +0.016 to +0.113); BM25 stays
  the default under the rule fixed in advance.
- **Platform.** Durable runs, approval sweep, runbook and alerts, chaos tests, paper-day replay gate,
  harness split into mixins, `mypy src` clean, Windows CI, supply-chain workflows.
- **Evaluation headline** (regenerated `docs/evaluation/latest.json`): execution primary-cause accuracy 0.98 over 50 scenarios (false-positive rate 0.06); research verdict accuracy 0.8333 over 30; golden investigation cause 0.9667, golden research verdict 0.8667 (13/15; G-RES-10/11 `mixed` diagnosed as near-threshold sampling, not tuned). Unchanged from the 0.8.0 numbers.

### Breaking changes

- **H11 API keys.** Outside dev, a raw key needs at least 256 bits of estimated entropy and 16
  distinct characters or the app refuses to start. Generate keys with `secrets.token_urlsafe(32)`.
- **F12 halts on by default** in paper trading: a member with no print gets no new order.
- **F10 ledger bodies v2.** New paper-ledger rows hash integer micro-dollars; 0.8.0 rows verify
  unchanged, but tools that rewrite ledger rows must write `money_v2`.
- **H6.** Private helpers moved from `ceap/harness/engine.py` into mixin modules; every public name
  is re-exported from `engine`.

### Fixed

- Critic `numeric_mismatch` check now honours printed rounding (e.g. '1.1 bps' vs 1.0864) and
  requires the attribute's other qualifiers for phrase matches; previously it lowered two correct
  T05 findings (including the venue-degradation headline) to 0.2.
- H3: `implementation_shortfall_bps` treats a non-finite `final_price` like `None` instead of
  returning NaN.
- E1: H2 reports NOT TESTABLE for a window with one market state instead of crashing (singular
  matrix); the crash happened once during the holdout opening and is ledgered.

- Local-model client refuses a checkpoint recorded for another (model, seed, temperature)
  instead of silently overwriting it on the first call.
- Paper dividends: a symbol that could leave the cache directory is refused.
- `ceap.eval.local_model --replay --write` writes the pooled aggregate; a single-seed live run
  no longer overwrites it (each seed process used to replace the others' numbers).
- Paper ledger hashes an integer quantity as the REAL column reads it back: an int 10 was hashed
  as 10 but read back as 10.0, so verification failed.
- Harness invariant 7 (an untraceable narrative is blocking) is asserted unconditionally; the
  test's feature flag read `engine.py`, which the engine split emptied of that code.

### Results (filled before the tag)

| Item | Value | Evidence |
|---|---|---|
| Local model, per-agent evals | plan valid 15/30, critic 35/45, untraceable numbers 0/39 (3 seeds, replay). Golden slice: not measured; the 6/9 first published here was mock-fallback output (corrected 2026-11-12, see Unreleased) | `docs/evaluation/local_model/` |
| Local model, injection obey rate per surface | question 3/10; retrieved doc, tool output, finding 0/10 each (40 attacks) | `ceap.eval.injection` |
| Judge kappa per dimension (50 items, bar 0.6) | hedged 1.00 (enabled); faithful 0.51, complete -0.03 (not enabled) | `ceap.eval.judge` |
| Execution evaluation (50 scenarios) | primary cause 0.98, false positives 0.06 | `docs/evaluation/latest.json` |
| Research evaluation (30 scenarios) | verdict accuracy 0.8333 (lane L3 re-run: 23/24 clear, 2/6 near-threshold) | `docs/evaluation/latest.json` |
| Golden set scores | investigation cause 0.9667, must-mention 1.00; research verdict 0.8667 (13/15) | `docs/evaluation/latest.json` |

**Golden 13/15, diagnosed (not tuned).** G-RES-10/11 (template `mixed`, RS19/RS21) are labelled
REJECT/COST_DRAG from the population truth: out-of-sample IC t 1.99 against the 1.0 bar. On the
realised 14-year sample the platform measures 0.79 and 0.93, so the `OOS_DECAY` branch fires before
the cost check (`research_assessment.py`), and the full-period net Sharpe interval [-0.16, 0.75]
makes it INCONCLUSIVE. This is a near-threshold sampling miss, not a code defect; the thresholds and
rule order were fixed before the result and are not changed after seeing it. Owner option A stands.

**Known issues (low, found in the 0.9.0 review, left for 0.10.0).** Paper fills never fill an
order when `lot_size` exceeds the per-session cap (it expires at TTL); the pre-trade sigma band is
zero for a name whose window prices never move, so every move is blocked; the durable-run
idempotency key is stable only for JSON-native arguments (all current callers). `industry_momentum`'s
Andrews lag divides by zero on constant residuals (degenerate input only). The B7 challenger
agreement is **not measured**: its comparison path matched neither model's recordings and marked
live primary replies unusable, so no number is published (llama replies kept in the cassette).
`factor_regression` reports R² ≈ 0.93 for a constant y, and `raw_betas_ex_self` returns huge betas
for a near-constant market series: the total sum of squares or variance lands a few ulps above zero
and passes the exact-zero guard; both need a tolerance.

### Tests

- 182 new behaviour tests: critical paths (staging, kill switch, approvals, critic, risk agent
  and risk maths), analytics and evaluation (research-study drivers, eval runs, report renderers),
  and the CLI, db/replay commands, replay harness, anchors and MCP client.
- Seven 30-75 s tests moved to the `slow` tier (600 s ceiling). Under parallel load they crossed the
  120 s default and pytest-timeout ended the xdist worker, losing its coverage data.
- `CEAP_TEST_TIMEOUT_SCALE` multiplies the `slow`/`nightly` ceilings on a loaded machine; CI leaves
  it unset. Local coverage recipe: `docs/testing/test-pyramid.md`.
- Coverage: 93 % branch across the whole `ceap` package (1326 tests: fast tier on 3 workers, slow tier serially, combined). The CI and nightly floor rises from 85 % to 90 %.
- Docs audit corrections to `docs/quant/quant.md` (a-priori hurdle, INCONCLUSIVE verdict, power figures, research score 25/30, the two numerical issues) are in `docs/quant/quant-errata-0.9.0.md`: quant.md is a pinned RAG corpus file, so editing it would move the corpus pin, the retrieval caches, the preregistered RAG results and the report cassettes. They are folded back in at the 0.10.0 corpus re-measurement.

**Fixed in the review:** a re-plan with step dependencies was always rejected (`depends_on` kept the
old step ids); a NaN paper equity passed the replay gate; a malformed judge calibration file could
enable dimensions; the judge put the model-written narrative into its prompt outside a data block.

The sections below are the lane notes, kept with their numbers and disclosures.

### Coordinator decisions (integration)

- Golden v1.1 signed by the owner, option A: template `mixed` stays REJECT.
- E1: the crash during the holdout opening and its re-run are accepted; disclosed in the ledger.
- G2: the generator stays bit-identical at 1.18x over 0.8.0, against the 50x target (a miss, stated).
- Retrieval: BM25 stays the default under the a-priori rule; the reranker (+0.065 R@3) is opt-in.
- Near-miss abstention not adopted; the leak stays 3/25.
- L5: the idle event-loop-lag bound was dropped as machine-dependent; the block-detection bar is kept.
- D5: test bar 490 -> 466, derived from the scaled delisting rule (500 names minus 17 mergers and
  17 performance delistings), not from a result.

### Integration fixes

- `mypy src`: 0 errors. The harness mixins share a typed `HarnessBase` (`ceap/harness/base.py`);
  `warn_return_any` is now on for `ceap.api.*` (the three remaining sites are annotated).
- B7 wired: the approval payload and `GET /approvals/{id}` carry an advisory `challenger` block from
  `approver_challenger_view` only when a challenger plan is present (off by default).
- H12: every broad `except Exception` in library code states its reason; a test enforces it.
- `ceap.rag.dense.ModelUnavailable` renamed `ModelUnavailableError` (ruff N818).
- `docs/api/openapi.json` regenerated.

### Lane L1: research (WS-E E1, E2)

- E1: preregistered, hash-locked 49-industry momentum study on free Ken French data
  (`docs/research/e1/`, `src/ceap/studies/industry_momentum.py`, `scripts/fetch_kf_industries.py`,
  `scripts/run_e1_industry_momentum.py`). Holdout 1990-2015 opened once: **NULL** (net 10 bps NW
  t 1.16 < 1.645; net Sharpe 0.23, CI [-0.12, 0.59]). In-sample t 1.86; ETF gate PASS (0.94);
  replication gate NOT RUN. One crash during the opening, ledgered and disclosed.
- E2: `docs/research/e2_why_r5_could_not_pass.md`, why stock-level R5 was predictably VOID on free data.

### Lane L2: AI evaluation (WS-B: local model, injection corpus, judge, challenger, AI SLOs)

#### Added
- **B1** `ceap.llm.ollama.OllamaLLMClient`: a local open-weight model (pinned: `qwen2.5:7b-instruct`
  primary, `llama3.1:8b-instruct-q4_K_M` challenger) behind `LLMClient`, using Ollama's JSON-schema
  `format` for structured output and the same Pydantic validation as the hosted adapter. Answers carry
  `provenance = local_model`. `LocalCassette` / `LocalCassetteClient` record and replay runs keyed by
  the canonical request hash (nonces masked), so CI scores the recorded replies with no Ollama.
  Live calls need `CEAP_LOCAL_MODEL=1`. Runner: `python -m ceap.eval.local_model --seeds 1 2 3`.
- **B4** `ceap.eval.injection`: 200 locally written attacks (4 surfaces x 10 styles x 5 goals,
  `injection_corpus_v1.json`, sha256-pinned), canary-based obey rate per surface with Wilson CIs;
  full sweep is `nightly` and needs `CEAP_LOCAL_MODEL=1`.
- **B5** `ceap.eval.judge`: rubric judge for the `judge` purpose (faithful, complete, hedged),
  50-item hand-built reference set (`judge_reference_v1.json`), Cohen's kappa per dimension; a
  dimension is enabled only when kappa > 0.6 (`enabled_dimensions()`).
- **B7** `ceap.eval.challenger.approver_challenger_view(primary_plan, challenger_plan)`: isolated
  approver-view hook (Jaccard, order agreement, tools only one model chose); `plan_agreement_eval`.
- **B8** `ceap.observability.ai_slo.AISLOMonitor`: per-purpose rolling plan-valid, repair, fallback,
  untraceable-number rate and p95 latency against objectives, Shewhart p-chart / log-latency SPC
  alerts, OTel GenAI span attributes and `gen_ai_client_*` metrics. `LLMRouter.slo` (or
  `ai_slo.default_monitor`) receives every routed call.

#### Results
See `docs/evaluation/local_model/README.md` (numbers, CIs, compute budget fixed before the runs).

### Lane L3: retrieval and critic

- **C2 hybrid retrieval (opt-in):** `ceap.rag.dense` adds bge-small-en-v1.5 embeddings, reciprocal
  rank fusion with BM25 and an ms-marco-MiniLM-L-6-v2 cross-encoder reranker, all local CPU models
  through the optional `fastembed`. Model outputs go to a hashed cache (`data/rag/`), so CI needs no
  model. Rerank variants beat BM25 on R@3 (rrf_rerank 116/124 vs 108/124, paired Δ +0.065
  [+0.016, +0.113]). BM25 stays the default because the reranker needs a model at query time
  (pre-registered rule). Details: `docs/ai-agents/rag-hybrid-0.9.0.md`.
- **Near-miss abstention: not fixed.** An abstain rule chosen on dev leaked 6/12 near-miss
  negatives on held-out, against 1/12 for the 0.8.0 gate, so it was not adopted. Still open at
  3/25.
- **Critic:** new deterministic statement-versus-evidence checks (`ceap.agents.critic_checks`) for
  contradictions, number mismatches and direction mismatches. Planted-fault detection went from
  6/15 to 15/15 on the existing set and on a new held-out set
  (`data/eval/critic_planted_heldout_v1.json`, committed before the change), with 0/10 false
  alarms on each. `assessment["flags"]` is new. Scorer: `python -m ceap.agents.critic_planted`.
- **Research clear-case accuracy:** a re-run on this branch (mock LLM, deterministic pipeline,
  critic change included) gave 23/24 clear verdicts, not 21/24. The one miss is RS02, a strong
  momentum premium rejected for COST_DRAG, the same kind of draw as the documented 0.7.1 miss. No
  bug was found in the verdict code, so nothing was changed. RS17 and RS18 got the right verdict
  but INCOMPLETE_ANALYSIS flags because the backtest step timed out (about 180 s each, machine
  under load from the other lanes); recheck them on an idle machine. Near-threshold accuracy was
  2/6 (bar 1/3).

### Lane L4: paper, execution, research data

Deferred items D5, D10, F5 (paper half), F10, F11 (paper-execution part), F12 (rest), F13, G2 from
`docs/review/2026-11-09/PLAN.md`. Everything new in the paper engine is off by default, so the
paper/backtest equivalence test and the daily forward-log job behave as in 0.8.0 unless
`config/paper.json` turns a feature on. Two defaults do change: ledger bodies (F10) and halts (F12).

#### Added
- **D5, 500-name synthetic universe.** `ceap.data.generator_params.RESEARCH_UNIVERSE_500` (names
  `X<tier><4 digits>`, tiers A/B/C at 40/40/20 %, every other name on NASDAQ), `synthetic_universe(n)`,
  `tier_of`, `is_nasdaq` and `UNIVERSES = {"core30", "synthetic500"}`. Use
  `HistoricalGenerator(symbols=RESEARCH_UNIVERSE_500)`. A decile is 50 names instead of 3. The 30-name
  universe and every existing dataset hash are unchanged. For any universe larger than 30 names, the
  membership events scale by `m / 30` and are drawn from a separate random stream. For 500 names that
  is 50 late listings, 17 mergers and 17 performance delistings. A test pins two 30-name dataset
  hashes computed before this change.
- **D10, delisting sensitivity on the real-data momentum book** (`ceap.data.real.momentum`).
  `build_months(..., delisting="shumway", perf_delist_return=-0.30, keep_missing_close=True)` and
  `delisting_band(...)` over a grid of 0, -30 %, -55 % and -100 %. The exit type is inferred from the
  path (last close below $1, or down 30 % or more over 60 sessions, means `performance`; anything
  else is a `merger` at 0 %), because free daily data carry no delisting codes. `Month.delisted`
  records each exit. The defaults reproduce the preregistered book exactly. The other settings are
  sensitivity analyses and never the primary test. Sources: Shumway (1997, J. Finance 52(1)) and
  Shumway and Warther (1999, J. Finance 54(6)), cited from memory.
- **F5, paper MOC at the closing auction** (`FillParams.moc_auction`). One session fills at most
  `auction_max_fraction * auction_share * ADV` (10 % of a 6 % auction, so 0.6 % of ADV). The cost
  is our order's impact on a seeded synthetic auction book (`ceap.execution.auction`), floored at 0.
  **Limit alignment:** `RiskLimits.max_work_sessions` makes the pre-trade check clip an order to that
  many sessions of fills at the per-session cap (`FillParams.session_cap_fraction()`).
  `fills.limit_alignment(params)` reports how many sessions the 25 % pre-trade limit needs at the
  10 % fill cap (3), and whether the order TTL lets the order finish.
- **F10, lots.** With `FillParams.lot_size` set, fills are whole lots. The fractional part stays on
  the order and gets a `lot_residual` event. An order smaller than one lot is cancelled with the
  reason `below_lot`. The default of 0 keeps fractional fills.
- **F11, LOB engine for paper fills** (`FillParams.engine = "lob"`, `ceap.paper.lob_exec`). The
  cost of each fill is measured on the seeded `LOBSimulator`. The order is cut into 10 market slices
  over 30 s, and the cost is the slippage of each slice against the mid just before that slice was
  sent. Fills stay at the close, so the daily equity identity still holds. Measured: about 0.3-0.4
  bps on a $200 name at every participation from 0.1 % to 50 %. The stylised book (300 shares a
  level) does not scale impact with size, and this limit is stated in the module.
- **F12, sigma fat-finger band and halts** (`ceap.paper.pretrade`). `price_band_sigma` (0 = off;
  6.0 is the plan's setting) with `price_band_cap` (10 %) and `sigma_window` (20) blocks a decision
  close that moved more than `min(cap, k * sigma_daily)`. **Halts (on by default,
  `block_halted`):** a member with no print gets no new order (rule `halted`), and a fill reject on a
  missing print now carries `halted: true`.
- **F13, dividends as cash events.** `ceap.paper.feed.Dividend(symbol, ex_date, amount)` and
  `MarketPanel.dividends`. A long receives `q * d` and a short pays it (the short dividend cost),
  booked after financing on the ex-date. The snapshot and the attribution gain a `dividends` key.
  The identity is now `d equity = mark_pnl + financing - borrow - costs + dividends`. No feed
  supplies dividends by default.
- **F13 data, dividend source (opt-in, default off)** (`ceap.paper.dividends`). The source is Yahoo
  Finance through the optional `yfinance` package: free, no sign-up, and not a project dependency
  (it is imported only for a download). Series are cached under `data/real_cache/dividends/`. Each
  fetched series is hashed with SHA-256, and a record goes to `revisions.jsonl` with
  `revised: true` when the vendor restates a series. The panel's `meta` carries the hashes in use.
  Enable it with `execution.dividends: true`; `execution.dividend_download: true` refreshes the cache
  first. Yahoo dividends are split-adjusted, which matches the split-adjusted Stooq closes.
  `config/paper.json` is unchanged.
- `PaperConfig.execution` (JSON block `execution`) carries `moc_auction`, `auction_share`,
  `auction_max_fraction`, `lot_size`, `engine` and `lob_window_s`. Unknown keys are refused.

#### Changed
- **F10, money in ledger bodies.** Every new `paper_ledger` row records `cash_delta`, `cost` and
  `price` as integer micro-dollars under `payload.money_v2`, and the HMAC (or SHA-256) link covers
  those integers plus `body_version: 2`. The REAL columns hold exactly `micros / 1e6`. A column
  edited without its integer breaks the hash on purpose. Rows without `money_v2` (written by 0.8.0)
  are hashed exactly as before, so an existing `var/paper/paper.db` verifies unchanged and the
  forward job just goes on appending version-2 rows. `reconcile` sums version-2 cash in integers.
  Day rows are in body v2 too. The snapshot JSON carries `money_v2 = {equity, cash}` in
  micro-dollars, the hash covers the equity integer, and the `equity` column is exactly
  `micros / 1e6`. 0.8.0 day rows verify unchanged; a test covers a chain with mixed v1 and v2
  rows. When every ledger row is v2, `reconcile` also compares ledger cash with the day's recorded
  cash in integers. The allowed gap is half a micro-dollar of rounding per event.
- **G2, `SyntheticMarketGenerator._trades`.** Per-trade columns are built as arrays: prices by
  fancy indexing, timestamps in one datetime64 pass with the session's UTC offset looked up once,
  and `Trade` objects filled through `__dict__`. Output is bit-identical (the v0.7.1 digest test
  passes). **Measured on the dev machine** (6 calls, 49,766 trades, best of 5, same process): 0.8.0
  0.307 s, 0.9.0 0.260 s, v0.7 1.10 s. That is **1.18x over 0.8.0 and 4.2x over v0.7**, not the 50x
  target. About two thirds of the remaining time is the per-second loop of `rng.lognormal` and
  `rng.random` calls, kept to preserve the random stream. A 50x path needs a new stream (one bulk
  draw per column), which would move every generator golden, including the owner-signed
  `golden_set.json`. Owner decision, not taken here.

#### Tests
- New: `tests/unit/paper/test_r090_l4_dividends.py` (dividend source, fixtures only, no network),
  `tests/unit/test_r090_l4_universe.py` (D5), `tests/unit/test_r090_l4_delisting.py` (D10),
  `tests/unit/paper/test_r090_l4_paper.py` (F5, F10, F11, F12, F13, ledger v2 on a v1 chain).
- Updated: `tests/integration/test_paper_controls.py` expects the new `dividends` attribution key.
- No golden moved.

### Lane L5: platform (harness, API, persistence, operations)

#### Changed
- **H6 harness split.** `ceap/harness/engine.py` (1,532 lines) is now a facade of 443 lines holding
  `AgentHarness.__init__` and `execute`; the rest moved without change into mixins
  `planning.py` (plan validation, governance tail, scoping, re-plan), `dispatch.py` (tool batches,
  agent runs), `approvals.py` (human gates, staging grants), `evidence.py` (evidence validation,
  disputes, hypotheses, case memory) and `common.py` (constants, `HarnessResult`, helpers).
  Every name `engine` used to define is re-exported, so `from ceap.harness.engine import ...` keeps
  working. The split is its own commit with no behaviour change.
- **G13 Docker.** Two-stage image: the build stage installs the hashed lock into `/opt/ceap`; the
  runtime stage copies that environment and removes pip, so nothing can be installed at run time.
  `deploy/compose/` is a reverse-proxy example (Caddy terminates TLS, adds security headers, keeps
  `/metrics` internal).
- **G12.** A manual `paper-daily` dispatch without a date now uses the New York date
  (`TZ=America/New_York`), not UTC. The NYSE calendar already raises outside 2000-2030; a test now
  pins that it fails loudly for 2031.
- **H11 API keys.** Outside dev, a raw key must have at least 256 bits of estimated entropy and 16
  distinct characters or the app refuses to start; the message never contains the key. A hashed key
  entry may carry `id=<name>`, and its principal is then `role:<name>`. Breaking for deployments
  using short raw keys outside dev: generate keys with `secrets.token_urlsafe(32)`.

#### Added
- **G5 durable runs** (`ceap/harness/durable.py`, migration `0005_run_journal.sql`). Each tool call
  has an idempotency key `sha256(task | step | tool | canonical args)`. With a journal, a completed
  call is written at the step boundary and a re-run of the same task id reads it back instead of
  calling the tool; a human decision for the same content hash is reused (resumable approvals).
  Opt-in in the platform with `CEAP_DURABLE_RUNS=1`; journal rows follow the results retention.
- **G8 approval TTL sweep** in the API `lifespan` (`ceap/api/loopmon.py`): every
  `CEAP_APPROVAL_SWEEP_S` seconds (default 60) PENDING approvals older than timeout + interval with
  no live waiter are expired, audited and counted (`ceap_approvals_swept_total`).
- **G9 operations.** `docs/RUNBOOK.md` (restart, stuck task, stuck approval, event-loop lag, audit
  chain break, disk full, API keys, replay gate); `deploy/prometheus/ceap-alerts.yml` (7 rules, each
  naming its runbook section); event-loop lag summary `ceap_event_loop_lag_seconds`.
- **G10 chaos tests** (`tests/unit/test_r090_chaos.py`): kill -9 in the middle of `run_day`
  converges to the uninterrupted ledger; kill -9 in the middle of an investigation resumes from the
  journal; an injected `disk I/O error` rolls back whole and the next write succeeds. The kill tests
  are `slow`.
- **G11 paper-day replay gate** (`ceap/harness/paper_gate.py`): replays the forward book into a fresh
  database and diffs `paper_days` (equity to 1e-6, state and snapshot as JSON); `render_status_page`
  writes the forward-log status page for Pages. Nothing is pushed.
- **H12.** Test that library code does not `print` (entry points and `evaluation.py`'s verbose
  console table excepted); `except Exception` in `harness/replay.py` now states why it is broad.

### Lane L6: engineering and docs (H1, H3, H4, H7, H10, H13-H15, I1, I2, I5)

#### Changed
- **H1 mypy.** No global `ignore_missing_imports`: untyped third-party packages are named one by one
  (jsonschema, yaml, pandas, sklearn). `ceap.domain` and `ceap.analytics` are type-checked with the
  full strict flag set (per-module override; mypy cannot set `strict` per module). Eight
  `no-any-return` sites in analytics got typed locals (annotation only, no behaviour change).
  `ceap.api` had every strict flag except `warn_return_any`; the three remaining sites were
  annotated at integration and `warn_return_any` is now on (see Integration fixes).
  `mypy src`: 0 errors in 207 files.
- **H3 NaN final price.** `implementation_shortfall_bps` now treats a non-finite `final_price` like
  `None` (no opportunity leg) instead of returning NaN, matching `shortfall_components`.

#### Added
- **H3** `tests/properties/test_r090_mutation_gaps.py`: kills the two 0.8.0 surviving mutants
  (non-positive fill mids, NaN final price) and widens the Hypothesis domain (prices 1e-2 to 1e5,
  quantities 1e-3 to 1e9). Nightly `mutation` job: mutmut 3.8.1 on analytics and domain with a
  surviving-mutant ratchet (`scripts/mutation_ratchet.py`, `tests/mutation/baseline.json`; the
  ceiling is `null` until the first Linux nightly measures it).
- **H4** `test-windows` CI job (windows-latest, fast tier, no coverage).
- **H7** `.github/dependabot.yml` (pip and actions, grouped, weekly); `codeql.yml`; `scorecard.yml`;
  release job now writes a CycloneDX SBOM, a build-provenance attestation and cosign keyless
  signature bundles for the wheel, sdist and SBOM. All actions pinned to commit SHAs.
- **H10** CI gate: modifying or deleting files under `tests/cassettes/`, `tests/agent/snapshots/` or
  `tests/contract/snapshots/` needs the `snapshot-change` label (new files are allowed).
- **H13** Coverage measured on Python 3.12 only. `benchmarks/` (pytest-benchmark 5.3.1) with a PR
  gate: base and head on the same runner, fail if any mean is more than 25 % slower.
- **H14** `.pre-commit-config.yaml`: ruff, mypy, banned words, docs check, OpenAPI freshness.
- **H15** `docs/api/openapi.json` checked in (`scripts/export_openapi.py`), diffed in the `contracts`
  job; nightly `api-fuzz` job runs Schemathesis 4.33.1 on the GET operations.
- **I1** `docs/adr/` 0001-0005. **I2** CI `api-reference` job (pdoc 16.0.0 for analytics and domain).
- **I5** learning path in LEARN.md; `docs/glossary.md`, `docs/how-to-extend.md`,
  `docs/exercises.md`, `docs/testing/test-pyramid.md`; metamorphic tests
  (`tests/properties/test_r090_metamorphic.py`) and CLI tests (`tests/unit/cli/`).

Job-only tools (mutmut, pytest-benchmark, schemathesis, pdoc, cyclonedx-bom, pre-commit) are pinned
in their jobs and kept out of `requirements.lock` (ADR 0003).

## [0.8.0] - 2026-11-09

The safety, research-realism, execution and AI-evaluation release: steps from the two-round review of
2026-11-09 (`docs/review/2026-11-09/PLAN.md`, owner decisions D1-D4), built by seven parallel lanes
and shipped as one combined PR. Full local suite with the API key unset: 935 passed, 0 failed.

### Results that moved, and why (read before comparing with 0.7.x)

- Research verdict accuracy 0.9667 -> 0.8333 (25/30, mock LLM): the synthetic premia were recalibrated
  to realistic strength (D1), the IC hurdle is now fixed a priori (D3) and INCONCLUSIVE exists (D4).
  Each pass bar is a 5 % one-sided lower bound derived in advance from the generator, not from a run;
  the clear-case subset (21/24) sits exactly on its bar.
- Owner-signed golden set, verdict accuracy 1.00 -> 0.8667 (13/15). G-RES-10 and G-RES-11 (template
  `mixed`, signed-off expectation REJECT) no longer get REJECT under the three-way rule. The
  signed-off expectations are unchanged; whether `mixed` should be REJECT or INCONCLUSIVE is an owner
  decision at the next sign-off (`tests/golden/golden_set_research_v1_1.json`, PENDING).
- New per-agent evaluation (B2, mock and hand-built cases): the critic detects 6/15 planted problems
  (0.40, 95 % CI 0.20-0.64), including 0/3 contradictions and 0/3 direction mismatches. This is the
  first measurement of the critic and a 0.9.0 work item, not a pass.
- Retrieval: 3/25 near-miss negatives leak a passage (0.12, CI 0.04-0.30); 0/10 plain negatives.
- Reproducibility fixes found during integration: allowlist derivation drew ids from the run's clock
  on first use only (reports differed by process history); process-pool timeouts killed every worker,
  losing results and changing clock reads. Both fixed with tests (thread/process parity test).

### Breaking changes in 0.8.0

Read these before upgrading from 0.7.1. Each is described in more detail in the entries below.

- **Failing narrative is replaced, not shipped (A2, owner decision D4).** Model prose that fails the
  number, evidence-id or citation audit no longer ships with a warning. The report carries the
  deterministic template narrative, the degradation `LLM_NARRATIVE_SUPPRESSED` and
  `critique.narrative_suppressed`; the rejected prose goes only to the audit log
  (`reporter.narrative_rejected`). Consumers that read the narrative of a flagged report now get the
  template text.
- **The policy fails closed without a universe (A3, D4).** With no symbol universe configured, any
  request that names a symbol is denied. Every symbol-bearing field is checked (lists, nested objects,
  schema properties marked `x-ceap-symbol`), and arguments are validated against each tool's JSON
  schema, so calls with extra or malformed arguments that used to pass are now refused.
- **Admins need approval for HIGH risk (A4, D4).** `risk:high` no longer skips the human approval for
  a HIGH-risk tool, and an admin cannot decide approvals, so an admin's request goes through four-eyes.
- **Untrusted MCP servers are denied (A5).** Every tool of a server outside
  `CEAP_MCP_TRUSTED_SERVERS` is denied, even when it declares itself read-only (in 0.7.1 it was
  treated as read-only). `CEAP_MCP_CATALOGUE_PINS` refuses start-up on a catalogue mismatch.
  `build_tool_registry` now returns a `PinnedToolRegistry`.
- **API keys can map to a named subject (A13).** A key mapped with `:subject=NAME` (or
  `CEAP_API_KEY_SUBJECTS`) acts as principal `user:NAME`, and four-eyes compares persons, not keys:
  two keys mapped to the same person can no longer request and approve the same action. Unmapped keys
  behave as before.
- **Audit and ledger chains are HMAC-keyed (A6).** New env var `CEAP_AUDIT_HMAC_KEY` (rotation via
  `CEAP_AUDIT_HMAC_OLD_KEYS`). Without it a published development key is used and reports say
  `dev_key: true`; set it for any database you want to trust. A database written with a key verifies
  only where that key (or an old key listed) is configured. Migration 0004 adds `hash_alg`; rows
  written before it stay plain SHA-256 as a sealed legacy prefix.
- **Process-pool tool isolation is server-only (G3).** `CEAP_CPU_TOOL_PROCESSES` defaults to 2 under
  `ceap serve` and 0 (threads) in the CLI, the library and the tests. Workers use `spawn`, so a script
  that sets it must keep its top level under `if __name__ == "__main__":`.
- **Coverage gate (H2).** CI measures branch coverage (`--cov-branch`) with a floor of 85 % (was 80 %
  line coverage). A PR that lowers branch coverage below 85 % fails.
- **LLM budget and rate limit on by default (A10).** Per-task caps (`CEAP_TASK_BUDGET_USD` 2.00,
  `CEAP_TASK_TOKEN_BUDGET` 400000) and a per-principal rate limit (429 with `Retry-After`) apply
  unless set to `off`.
- **Renamed or changed public API.**
  - `ResearchVerdict` gains `INCONCLUSIVE` (contracts change); code that assumed only PROMOTE and
    REJECT must handle it.
  - Research templates `momentum_near_threshold` and `reversal_weak` are renamed
    `momentum_realistic` and `reversal_realistic` and recalibrated (D1); the old names survive only as
    the pre-0.8 calibration used by the signed-off golden set.
  - Prompts move to `report@2`, `research_report@2`, `reporter_claims@2` and `reporter_prose@2`, and
    are hash-verified at load (`PromptIntegrityError`).
  - Cassette `prefix` interactions need `allow_prefix=True`; unused interactions fail
    `assert_consumed`.
  - `data_block` takes a `nonce`; the closing tag repeats it (`</data nonce="...">`).
  - `Database.verify_chain(expected_head, expected_rows)` and `PaperLedger.verify(book, anchor)` gain
    optional anchor arguments; `audit_hash` takes optional `keys`.
  - `ShortfallComponents` gains `fill_rate` and `opportunity_priced`.
  - `market_impact_bps` and `temporary_impact_bps` raise `DeprecationWarning` (removal in 1.0).
  - The mock LLM reports zero tokens and zero cost (it reported characters / 4).
  - The research IC hurdle is t >= 3.0 for a searched result (was 0.40), so fewer synthetic
    scenarios PROMOTE.

- Narrative guardrails (0.8.0 lane 1, plan WS-A steps A1, A2, A7, A8, A11, A12, A15, A19, A20).
  **Breaking (decision D4):** model prose that fails the number, evidence-id or citation audit is no
  longer shipped with a warning; the report carries the deterministic template narrative, the
  degradation `LLM_NARRATIVE_SUPPRESSED` and `critique.narrative_suppressed` (sha256, length,
  reasons), and the rejected prose goes only to the audit log (`reporter.narrative_rejected`).
  - A1: typed placeholders. The model names a figure as `{fact.key}`; code renders it with its unit.
    The free-text audit is hardened: unit-matched conversions only, no sign flip unless a direction
    word agrees, and no small-integer exemption (line-start list markers and fact-list lengths
    still trace). The review repro (`-12.34 bps` and `1234 bps` against
    `{slippage_bps: 12.34, fill_rate: 0.4321}`) now fails; `43 pct` passes as the correct
    percent form of 0.4321. Prompts `report@2`, `research_report@2`, `reporter_claims@2`,
    `reporter_prose@2`.
  - A7: per-claim citation mapping (`claim_citations`): a policy claim needs a verified citation on
    its own sentence. The mock now writes each policy claim as its own line and cites it there.
  - A8: data blocks NFKC-normalise and strip zero-width characters, escape tags whitespace-tolerantly
    and carry a random nonce that the closing tag repeats (`</data nonce="...">`).
  - A11: planner string arguments are allowlisted (enum, else a short plain value); a step with an
    unknown data scope is dropped. A12: the planner and critic repair requests carry only the
    structured problem list, in a data block, never the model's reply.
  - A15: prompt `version:sha256` pinned in `llm/model_pins.toml` `[prompts]` and verified at load
    (`PromptIntegrityError`).
  - A19: cassette matching is exact by default; `prefix` needs `allow_prefix=True` (cache tests
    only); `assert_consumed` fails unused interactions; nonces are masked in the match key and the
    stored body. Cassettes regenerated through the existing writer.
  - A20: the mock client reports zero tokens and so zero cost (it used to report characters / 4).
- Research realism, WS-D (D1-D23 except D5 and D10; synthetic evaluation only, no real-data result):
  - D1: research templates carry a `calibration` label. `momentum_near_threshold` becomes
    `momentum_realistic` (premium 1800 -> 1100 bps: rank IC 0.025, gross Sharpe 0.42) and
    `reversal_weak` becomes `reversal_realistic` (transient share 0.40 -> 0.18: five-day rank IC
    0.024); the other templates are described as "Stylised" with their multiple of a real premium.
  - D3: a-priori IC hurdle, t >= 3.0 for a searched result and 1.645 for one preregistered test
    (was 0.4, read from the null sweep). The null sweep now measures the false-PROMOTE rate at the
    hurdle: 0 of 500 seeds at 3.0 (sweep hash unchanged, `ef474869e5fa…`); per-seed vector in
    `docs/research/null_sweep_per_seed.json`; sweep runs logged in `data/reference/sweep_ledger.jsonl`
    with the generator version (D20, D21).
  - D4: new `ResearchVerdict.INCONCLUSIVE` (contracts change) when every flag is statistical and the
    95 % interval of the full-period net Sharpe covers both 0 and 0.5. The golden test that listed a
    non-existent `MONITOR` verdict now reads the enum.
  - D2: adversarial generator families (hidden factor, regime switching, GARCH plus jumps, premium on
    market beta) on a separate random stream, so every existing dataset hash is unchanged; power and
    false-positive curves in `docs/research/adversarial_power.json` (`ceap.analytics.adversarial`).
  - D7 leakage canary through `simulate_backtest`; shuffled-label `t_max` 4.0 -> 3.0. D8 trial
    accounting (`ceap.analytics.trial_accounting`; the DSR trial count is floored at the 24-way search
    or the preregistered variants). D9 PBO by CSCV and stationary block bootstrap
    (`ceap.analytics.overfitting`). D11 Newey-West over gaps with the Andrews bandwidth. D12
    equal-weight baseline, paired bootstrap and Diebold-Mariano test and coefficient paths for the
    ridge; it now PROMOTEs only if it also beats the baseline. D13-D19 in
    `ceap.analytics.risk_research` and `ceap.analytics.alpha_decay`; D14 `ceap.data.real.vintage`;
    D20 `scripts/rebuild_study.py`; D22 model inventory (`ceap.ml.inventory`, new `ModelCard` fields);
    D23 `ic_ir_annualised` labelled descriptive and the flat borrow assumption stated.
  - Research-evaluation accuracy bars are now a priori: 5 % one-sided Poisson-binomial lower bounds at
    parameter-derived hit probabilities (`ground_truth.hit_probability`, `accuracy_bar`). Clear 0.85 ->
    0.875, near-threshold 0.5 -> 1/3, all 0.85 -> 0.80. The old bars predate the D1 recalibration;
    measured 21/24, 2/6 and 25/30 meet the new bars (clear exactly at its bar).
  - Golden set: the owner-signed `golden_set.json` 1.0.0 is unchanged. Its items on the renamed templates
    run the pre-0.10 calibration they were authored against (`pre010:RSnn`, `golden_scenario_id`). The
    three items on the recalibrated templates are in `tests/golden/golden_set_research_v1_1.json`
    (1.1.0, sign-off PENDING, owner sign-off required); `scripts/build_golden_set.py` writes only that
    file unless `--main` is given.
- **Breaking (D4), policy lane (A3):** the policy engine validates arguments against each tool's JSON schema, checks every symbol-bearing field at any depth (lists and nested objects too, plus schema properties flagged `x-ceap-symbol`), bounds the whole argument tree (string length, item count, depth, total values) and coerces `limit` to a whole number before its bound. With no universe configured, a request that names a symbol is denied.
- **Breaking (D4), A4:** HIGH-risk tools always need a human approval; `risk:high` no longer skips it. Admins cannot decide approvals, so an admin's request goes through four-eyes.
- **Breaking, A5:** every tool of an MCP server outside `CEAP_MCP_TRUSTED_SERVERS` is denied, even when it declares itself read-only. Each server's tool catalogue is hashed at start-up; `CEAP_MCP_CATALOGUE_PINS=server=<sha256>,...` refuses start-up on a mismatch, and `PinnedToolRegistry.verify_catalogue` refuses on drift.
- **A13:** API keys can be mapped to a named subject (`:subject=NAME` on a `CEAP_API_KEY_HASHES` entry, or `CEAP_API_KEY_SUBJECTS=role:xxxxxxxx=NAME`). A mapped key's principal is `user:NAME`, so four-eyes binds to the person, not the key.
- **A14:** `GET /approvals/{id}` returns the tool id, its exact arguments, the content hash and `hash_verified`. `POST /approvals/{id}` accepts `content_hash`; a mismatch is 409. The gateway re-checks the hash after the decision and refuses an approval whose arguments changed. The review payload no longer says there is no pre-trade cost model.
- **A16:** per-agent least privilege: each agent's tool calls run with its own capability set and tool allowlist (`ceap.policy.agents`), intersected with the principal's.
- **A18:** OWASP LLM Top 10 mapping in `docs/threat-model/owasp-llm-top10.md`.
- Audit log and paper ledger chains are HMAC-SHA256 keyed (A6). The key comes from
  `CEAP_AUDIT_HMAC_KEY`, never the database; without it a published development key is used and
  reported as `dev_key: true`. Earlier keys go in `CEAP_AUDIT_HMAC_OLD_KEYS`. Before this change,
  anyone who could write the SQLite file could flip an approval, recompute the hashes and still pass
  `verify_chain()`. `verify_chain(expected_head, expected_rows)` now also catches a cut-off tail. New
  `ceap db anchor` writes the chain heads to a file you can commit to git, plus a `.sha256` sidecar for
  an OpenTimestamps proof; `ceap db verify-chain --anchor FILE` checks against it. Migration 0004
  adds `hash_alg` per row. Rows written before 0004 stay plain SHA-256 and still verify, as a sealed
  legacy prefix. The forward and simulated log CSVs stay plain SHA-256 so anyone can still verify
  them; the forward-log export format is unchanged.
- DB-backed kill switch (F11): `ceap db kill-switch set|clear|status`. Each set and clear is an
  append-only row plus an audit entry in the same transaction. The paper job and MCP `stage_orders`
  both check it; `CEAP_PAPER_HALT` still works. A halt raised by the DB switch logs
  `source: kill_switch_db` instead of `env`.
- `stage_orders` runs pre-trade checks before staging (restricted, hard to borrow, per-name and gross
  limits; `ceap.paper.pretrade.check_staged_orders`) and reports `risk_blocks`. With a database, staged
  sets persist in `staged_orders` (one row per `staging_id`), so a retry or a restart finds the same set.
- SQLite durability (G7): file databases use `synchronous=FULL`. `Database.arun` times out
  (`arun_timeout_s`, default 30 s; `DatabaseTimeoutError`). `/health` adds `writer_queue`,
  `write_probe_ok` and `write_probe_ms` (the probe takes the write lock and releases it without
  writing), and reports `degraded` when the probe fails.
- Redaction (A9) also catches `name=value` secrets (`password=`, `api_key:`, `ANTHROPIC_API_KEY=` and
  other `*_KEY`/`*_TOKEN` names), phone numbers and high-entropy opaque tokens. Hex content hashes and
  readable ids are left alone.
- Knowledge admission (A17): a document with instruction-like text ("ignore previous instructions",
  role or prompt markers, requests to reveal prompts) or marked superseded is not indexed
  (`KnowledgeBase.rejected`). `search(..., as_of=)` serves only documents with
  `effective_date <= as_of` and, per `doc_key`, the latest version in force. The shipped corpus passes
  unchanged, and neither the corpus nor the RAG calibration changed.
- Implementation shortfall (F9): `ShortfallComponents` gains `fill_rate` and `opportunity_priced`. A
  partial fill with no final price now reports `opportunity_priced: false`, where before it silently
  showed a zero opportunity cost.
- `data/reference/ATTRIBUTION.md` says that the knowledge documents are fictional and that
  `classification: internal` is test metadata (I3).
- Execution (WS-F, lane 6): new package `ceap.execution` with an event-driven limit order book
  (price-time FIFO queues, millisecond clock, order-entry and market-data latency, icebergs, passive
  fills only when traded through, informed-flow adverse selection; F1), propagator impact driven by
  the bot's own fills with a fit reported against the truth (F2), TWAP/VWAP/POV/Almgren-Chriss behind
  one `Schedule` interface with exact whole-share allocation (F3), child-order logic (F4), an auction
  uncross with endogenous auction impact (F5, simulator half), a predicted-vs-realised cost loop in
  SQLite with weekly recalibration and a drift alert (F6), markout curves by venue and liquidity flag
  with bootstrap CIs plus a routing table (F7), and tick, round-lot and LULD rules (F12 part). Guide:
  `docs/execution/execution.md`; COOKBOOK recipes 44-48. Deferred to the owning lanes: paper MOC caps
  (F5 paper half), F10, F11, F13, and the sigma fat-finger band (F12 rest).
- Attribution: `regression_attribution` (F8), a cross-sectional ridge regression giving each cause a
  bps share with bootstrap CIs, next to the heuristic scores. Off by default; turn on with
  `attribute_causes(..., method="regression")` or `CEAP_ATTRIBUTION=regression`. With the flag off the
  output is byte-identical to 0.7.1.
- Generator speed (G2): `SyntheticMarketGenerator._trades` vectorised with the random draws in the
  same order, so every dataset is bit-for-bit unchanged (SHA-256 of three full scenarios identical
  before and after; v0.7.1 digest pinned in a test). About 5x on `_trades` and 10x on `generate`
  on the dev machine, short of the plan's 50x target (the rest is per-print object construction).
  The ML registry's generator *source* hash changes as a result; model outputs do not.
- Cleanups (F14): `market_impact_bps` and `temporary_impact_bps` now raise `DeprecationWarning`
  (removal in 1.0); `london()` documented as a deprecated alias of `session_time`; `vwap_by_bucket`
  uses one `bincount` pass. Markouts to the close use each fill's own session (`QuoteIndex.last_timestamp_on`).
- AI eval (B2): per-agent evals in `ceap.eval.agents`. They cover planner tool recall and
  precision against ten gold plans, critic detection of planted faults by kind plus its false-alarm
  rate, and reporter faithfulness, claim-drop, planted-claim catch rate and citation-verified. Each
  rate has a 95 % Wilson interval and an evidence class (`mock` until B1).
- AI eval (B3): a metric sensitivity test. Five degraded clients (`ceap.eval.clients`) must move
  all eight model metrics in the expected direction.
- AI eval (B6): an out-of-distribution golden split, `tests/golden/golden_ood_v1.json`, with 15
  items from three held-out cause-combination templates, scored per template. It is a separate
  versioned file, so the 45 signed-off items and their hash are unchanged. The split is
  `PENDING_OWNER`: it needs the `golden-change` label and owner sign-off.
- Docs (B9): `docs/ai-agents/ai-agents.md` now says which agents call the model (planner, critic,
  reporter) and which are deterministic.
- Retrieval eval (C1): `eval.json` version 2 adds 119 queries (paraphrase, typo, multi-hop,
  near-miss negative), for 159 in all. `extended_report` scores them by category with Wilson
  intervals, and BM25's weak spots (typos, multi-hop, near-miss leaks) are recorded without tuning.
- Corpus governance (C3): `src/ceap/eval/corpus_pin.json` pins each document's version, date and
  body hash. `ceap.eval.corpus` fails on unpinned or unbumped edits and future dates, and warns on
  stale documents and on release-note wording in corpus files.
- `ceap evaluation-report` gains agents, sensitivity, ood, retrieval and corpus sections.
- G1: CPU work is off the event loop. `validate_request` and `validate_research_request` run in a
  worker thread (`asyncio.to_thread`) from the API and from `Platform.investigate`/`research`;
  `DatasetStore` holds one lock per scenario key instead of one global lock (same key generates once,
  different keys in parallel) and gains `aget`; `CEAP_PREWARM_DATASETS` generates datasets at start-up
  off the loop (the Docker image pre-warms `T01`). A cold T01 took 30 s on this machine (77 s under
  load in the review) inside the request, and `/health` waited behind it; `/health` now answers
  while a dataset generates (`tests/integration/test_r080_offloop.py`).
- G3: CPU-bound MCP tools declare `cpu_bound=True` (annotation `cpuBound`, `ToolMetadata.cpu_bound`)
  and run their heavy part in a killable process pool (`ceap.mcp.cpu_pool`, `CEAP_CPU_TOOL_PROCESSES`).
  A step or task timeout, or a cancel, now terminates the worker; before, `wait_for` could not
  interrupt `run_backtest`. The API server runs 2 workers and starts them before it serves; the CLI,
  the library and the tests use threads. Each worker is its own slot, so a timeout terminates only its
  own call. The caller's dataset is sent to the worker, and a research report is identical in process
  and thread mode, with the same clock reads. `run_backtest` and `evaluate_signal` use the pool;
  `combine_signals` runs in a thread because it writes to the in-process trial ledger.
- A10: the LLM budget is on by default and race-free. Every call reserves its upper-bound estimate
  before it is sent and settles after; per-task caps on calls, tokens (`CEAP_TASK_TOKEN_BUDGET`,
  default 400000) and USD (`CEAP_TASK_BUDGET_USD`, default 2.00; `off` removes it). A circuit
  breaker skips the primary model after 5 consecutive failures for 60 s. A per-principal token bucket
  (`CEAP_RATE_LIMIT_PER_MINUTE` 60, `CEAP_RATE_LIMIT_BURST` 20) answers 429 with `Retry-After` on the
  task-starting endpoints.
- G6: paper dead-man's switch. `paper-daily.yml` opens or updates a `paper-failure` issue when a run
  fails (`issues: write`); the new weekly `paper-staleness.yml` fails and opens a `paper-stale` issue
  when the `forward-log` head is more than 2 NYSE sessions old
  (`scripts/check_forward_log_staleness.py`). README points to the last recorded session and asks the
  owner to protect `forward-log` against force-push (not changed here: rulesets are the owner's).
- H2: CI and nightly measure branch coverage (`--cov-branch`); the floor is the measured total
  rounded down (85 %), target 90 %.
- H5: repository-integrity tests (those reading `scripts/`, `docs/`, committed data or `.github/`)
  moved from `tests/unit` to `tests/repo`, which resolves the root explicitly (`tests/repo/repo_root.py`,
  `CEAP_REPO_ROOT`); `tests/unit` reads nothing outside `src` and `tests`.
- H8: `pytest-timeout` (dev dependency, lock regenerated): 120 s per test by default, 600 s for
  `slow` and 1800 s for `nightly` unless a test sets its own; `--durations=20` on every run.
- H9: one definition each for the `slow` and `nightly` markers. Nightly-marked sweeps skip unless
  `CEAP_NIGHTLY=1`, which `nightly.yml` did not set, so they never ran in CI; it now sets it.

## [0.7.1] - 2026-11-08

Patch release after 0.7.0: release-gate fix, golden-set owner sign-off and the R5 v2 write-up
(AshJha0/AgenticAICashEquities#6, #7). No change to analytics, models or evaluation numbers.

- Documentation brought up to date: the GitHub Pages landing page (`docs/index.html`) still described
  0.3.x and is rewritten for 0.7.1 (unverifiable cross-language and count claims removed); the README
  "read this first" box now says what is synthetic and what is real; COOKBOOK recipe 7 described the
  removed silent mock fallback and unpinned models; SPECIFICATION marked as the original 0.3 spec;
  paper job and golden sign-off described as done across the docs.

- Release gate: only the latest run of the `CI` workflow on the tagged commit decides
  (`scripts/release_check.py`, `GATING_WORKFLOWS`). It used to require every check run on the commit
  to be green, so the daily paper job (which also reports on main commits) could block a tag; the
  v0.7.0 gate was refused by a correctly refused paper session before the forward start. The gate job
  gains `actions: read`.
- Golden set v1.0.0 signed off: the repository owner checked the ten `signoff_sample` items on
  2026-11-08, all OK. Recorded in `tests/golden/golden_set.json` (`signoff`), the evaluation page and
  `docs/evaluation/latest.json` (regenerated: measured numbers unchanged; the labelled local cost
  estimate moves from $0.1987 to $0.1995 per investigation because the 0.7.0 prompts changed).
- R5 v2 attempt, published VOID before registration (`docs/research/result_momentum_2026-11-v2.md`).
  With the hand-downloaded Stooq bulk file as primary vendor, in-sample point-in-time coverage was
  74.9 % against the required 95 % (Stooq lacks delisted names; 337 of 606 series start after 2004).
  The Stooq vs yfinance cross-check passed at 99.56 % within 10 bps. No v2 preregistration was
  locked, no test statistic was computed, and the 2024-2025 pseudo-holdout is unspent. New script
  `scripts/r5v2_insample_checks.py` writes `docs/research/v2/`.

## [0.7.0] - 2026-11-08

R5 (real-data momentum study, published VOID) and RP (paper trading with desk controls) of the
2026-11-07 plan, shipped as one combined PR; the CI run that gates the tag is linked from that PR.
The paper forward log runs on synthetic data; the real Stooq feed exists but stays off (holdout unspent).
Final release docs pass, with every number sourced and labelled by evidence class.

### 0.7.0 integration

- 0.6.0 was merged (AshJha0/AgenticAICashEquities#4) but not tagged: its Python 3.11 CI job failed on
  cassette replay, so its content ships in this release (owner decision 2026-11-08).
- Cassette match keys are OS- and Python-stable. `request_sha256` rounds floats, and long decimal
  literals inside prompt text, to 9 significant digits, and masks evidence digests (as the report
  goldens do). Causes found by CI: last-ulp differences between Windows and Linux builds, and Python
  3.12 summing floats with compensation where 3.11 adds left to right. Stored bodies and wire requests
  stay exact. Regression tests in `tests/contract/test_cassette_match_os_stable.py`.
- `docs/quant/quant.md` is part of the RAG knowledge corpus, so release notes no longer go there; the
  0.7.0 numbers live in `docs/quant/real-data-and-paper.md`. A draft that added them to quant.md moved
  the calibrated support gate (2.5 to 2.3) and the chunking winner. quant.md now carries two short
  factual corrections and the RAG eval is unchanged. Cassettes and report goldens re-recorded (diff:
  corpus hash only).
- `test_paper_daily_workflow_is_disabled_by_default` checks the workflow text instead of importing
  PyYAML, which is not a project dependency.

### R5: real-data momentum study (PLAN items 5.1-5.5, published VOID)

- R5 5.1: preregistration of the 12-1 momentum study (`docs/research/prereg_momentum_2026-11.md`), second-read and committed with its SHA-256 in `docs/research/prereg_lock.json` before any price data was read; every price loader refuses to run on a hash mismatch or past 2023-12-31 (the pseudo-holdout).
- R5 5.2: approximately point-in-time S&P 500 membership (`ceap.data.real.universe_pit`, `data/reference/sp500_changes.csv` from fja05680/sp500 reconciled against Wikipedia, CC BY-SA attribution in `data/reference/ATTRIBUTION.md`), dated ticker map and a monthly coverage report.
- R5 5.3: Stooq and yfinance daily loaders reading a gitignored local cache (`data/real_cache/`, never committed), `scripts/download_daily.py` (yfinance; Stooq now serves a browser check to scripts and is not bypassed), and a data quality gate with planted-defect tests.
- R5 5.4: real-data cost schedule `ceap.analytics.real_costs` (5/10 bps half-spread, square-root impact Y = 0.7 at USD 100M, borrow GC plus hard-to-borrow, Corwin-Schultz as sensitivity) with known-answer tests.
- R5 outcome: the study is published as **VOID** (`docs/research/result_momentum_2026-11.md`). Coverage was 75.1 % against a required 95 %, and the Stooq cross-check was not run. No test statistic was computed and no verdict is issued. Items 5.6 and 5.7 were not run, and the pseudo-holdout is recorded UNSPENT in `prereg_lock.json`. The 5.5 breakeven figure (-38 bps) partly reveals the sign of gross returns; this is disclosed in the result document.
- R5 5.5: UMD replication gate `ceap.analytics.factor_check` (correlation >= 0.5 or PIPELINE SUSPECT), FF5 + UMD loadings, per-year and crash-month tables, breakeven cost; `data/reference/ff_factors.csv` from the Ken French library.

### RP: paper trading (docs/review/2026-11-07/RP_PLAN.md)

paper, synthetic or free daily data, not trading. No order leaves the process; no model is called.

#### Added
- **Simulated fill engine** (`ceap.paper.fills`): staged orders fill at the next session's close (the
  backtest's t+1 convention) with the backtest's own `CostModel`, `FinancingModel` and `PositionCaps`
  imported, a 10 % ADV participation cap with carry-forward, rejects for missing prices, non-members and
  SSR, order expiry, and splits applied to positions and open orders.
- **Append-only ledger** (migration `0003_paper.sql`): hash-chained events, daily snapshots, a forward
  log and a separate simulated log; UPDATE and DELETE refused by triggers; `ceap paper verify` checks
  the chains and reconciles positions, cash and equity against the fills.
- **Daily job**: `ceap paper run-day --date D` (idempotent, one transaction per session, catch-up),
  `ceap paper backfill --from --to` (simulated log only), `ceap paper report`, `export`, `verify-csv`.
  Staging goes through the same `QueuedApprovalGateway`; principal `paper-auto` decides only in
  `CEAP_ENV=dev` or with `--allow-paper-auto`, audited.
- **Forward record** from `forward_start` in `config/paper.json` (2026-11-09), with Sharpe and a 95 % CI,
  the minimum days to distinguish it from the in-sample Sharpe, and a CSV export with `schema_version`.
- `.github/workflows/paper-daily.yml`: scheduled after the US close, appends to branch `forward-log`;
  disabled until the repository variable `PAPER_DAILY_ENABLED` is `true`.

- **Desk controls (daily frequency).** Pre-trade checks on every staged order (restricted list,
  per-name and gross limits, max 25 % of ADV per order, fat-finger price band at staging and at fill),
  blocked and audited; kill switch `CEAP_PAPER_HALT`; hard-to-borrow names cannot increase a short;
  NYSE calendar (no run on holidays, half days flagged); end-of-day reconciliation with a break report
  (ledger vs fills, target vs actual drift) that fails the run (exit 4, nothing committed); daily P&L
  attribution (signal, drift, financing, borrow, costs); a markdown and JSON artefact per session; every
  order and fill carries its staging and approval ids.
- Capped fills keep working at the cap until filled or replaced (the `rf-data` backtest convention).

- **R5 feed shim** (`ceap.paper.feeds_real`, registered as feed `"stooq"`): builds the paper panel from
  the local Stooq cache with R5 point-in-time membership, lagged 20-day ADV and the 5.4 spread schedule.
  It refuses any row inside the unspent R5 pseudo-holdout (to 2025-12-31). `config/paper.json` stays on
  the synthetic feed with `in_sample_sharpe` null, because no Stooq files are cached and R5 is VOID.

#### Changed
- Schema version 3. `tests/unit/test_r4_persistence.py` now asserts `LATEST_VERSION >= 2`.

## [0.6.0] - 2026-11-08

Rounds R3A, R3B, R3C, R2B and R3E of the 2026-11-07 plan, shipped as one combined PR; the CI run that
gates the tag is linked from that PR. No live model call (D15): model-dependent results are labelled NOT RUN LIVE.

- Release docs pass: README, LEARN, COOKBOOK, architecture overview, DIAGRAMS (new diagram 11: LLM layer,
  agents, RAG, ML, evaluation), INDEX, SPECIFICATION, evaluation page; PLAN.md release mapping corrected and
  owner decisions of 2026-11-08 recorded (D18-D22).
- Cassette match keys are OS-stable: `request_sha256` rounds floats, and long decimal literals inside
  prompt text, to 9 significant digits before hashing (stored bodies and wire requests stay exact).
  Found by Linux CI: Windows-recorded cassettes differed only in last-ulp digits of TCA metrics.
  Regression test `tests/contract/test_cassette_match_os_stable.py`.
- Performance: `_slice_by_time` caches the sorted series and timestamp keys once per dataset; one
  investigation about 3.4 s instead of about 10 s (measured locally, one machine, not a benchmark).

### R3E: evaluation that measures what it can and labels the rest (3E.1-3E.4)

No live model call (D15); every live-only metric is printed `UNKNOWN / NOT RUN LIVE`.

- 3E.1 golden set `tests/golden/golden_set.json` v1.0.0: 30 investigation and 15 research items
  with expected findings from the scenario specifications (primary cause, must-cite evidence by
  tool, must-not-claim causes; verdict, must and must-not flags), frozen by an items hash
  (`ceap.golden`); edits need the `golden-change` label (new CI job) and owner sign-off of 10
  sampled items (PENDING for 1.0.0).
- 3E.2 `ceap evaluation-report` (`ceap.evaluation_report`): capability, safety (4 faults x 4
  invariants), robustness (rungs per fault), cost (labelled local estimate) and live-only rows.
  Full run (mock LLM): primary accuracy 0.98, coverage 0.98, false-positive rate 0.06, research
  verdicts 29/30, golden primary 0.9667 and verdicts 1.00, safety invariants 1.00.
- 3E.3 `docs/evaluation/history.csv` (0.4.0 and 0.5.0 as recorded, 0.6.0 measured),
  `docs/evaluation/latest.json`, and `scripts/check_docs.py` in CI (annotated numbers vs
  `latest.json`, landing-page results need a source or UNKNOWN label, the generated table, every
  documented `ceap` command parses).
- 3E.4 drift monitors (`ceap.drift`, `tests/contract/test_drift_monitors.py`, own CI step):
  cassette staleness, RAG recall/MRR floors, golden-set version, cassette models pinned, SDK
  version vs pin and cassettes; each with a failing fixture.

### 0.6.0 integration

- Merged `r3c-wiring` (R3A, R4, R3C, R3B) and `r2b-ml` on `release-0.5.0`. R2B hooks applied:
  `run_calibration_evaluation`, one shared `TrialLedger` for the alpha and backtest servers,
  `ResearchRequest.combine_signals`. Model cards relabelled to the R2T generator hash (model hashes
  unchanged). Report goldens regenerated; verdicts unchanged.

### Round R3C (retrieval half: 3C.1, 3C.2, 3C.5)

- **Breaking:** knowledge retrieval is Okapi BM25 (stdlib) instead of hashed TF-IDF;
  `ceap.rag.embeddings` (`Embedder`, `HashingEmbedder`) is removed and `tokenize` moved to
  `ceap.rag.text`. The knowledge tool no longer calls itself "semantic search". `KnowledgeBase`
  takes `chunking` and `min_support` instead of an embedder; `search` gains `classifications` and
  `min_support` and returns nothing when no chunk clears the support gate ("no policy found").
- Chunks and hits carry document, section, version, effective date and classification; the
  knowledge tool returns a SHA-256 `corpus_hash`.
- Three chunking strategies (`section`, `sentence`, `fixed` with overlap) compared by a retrieval
  eval (`data/reference/knowledge/eval.json`, 20 train / 10 held-out / 10 negative queries written
  before tuning; `python -m ceap.rag.evaluation`). Held-out recall at 3 is 1.00 and MRR 1.000;
  CI floor 0.80 / 0.70; all negatives empty.
- Corpus growth: front-matter versioning on every knowledge document, a new
  `09_attribution_thresholds.md` (kept equal to `Thresholds` defaults by a test) and
  `docs/quant/quant.md` via `manifest.json`.

- R2B (ML, D12): change-point detection on per-minute technology and feed metrics (robust z, CUSUM, PELT; `ml/anomaly.py`) with an alarm threshold fitted to a stated cost and ARL0 >= 20 sessions, consumed by the engineering and market agents as change-point findings that name the deployment lag.
- R2B: ridge signal combination with purged, embargoed walk-forward CV; every penalty and single signal is a ledger trial; the out-of-sample deflated Sharpe is reported next to each signal (`analytics/ml_signals.py`, MCP tool `alpha.combine_signals`).
- R2B: dynamic leakage tests (perturbation and shuffled-label) for every signal and feature; a planted look-ahead fails both (`analytics/leakage.py`).
- R2B: held-out isotonic confidence calibration for the change-point finding: Brier 0.0110 raw vs 0.0094 calibrated on seeds 100-199, so it ships. Added the `BaseAgent.calibrated` hook; the other confidences are now documented as heuristic.
- R2B: model registry and cards (`ml/registry.py`, `data/models/`, `docs/ml/`): loaded by hash, labelled "fitted to synthetic generator <hash>", PSI drift warning at load, and a nightly reproduction test across thread counts.

### R3C wiring: 3C.3 retrieval in the planner and policy, 3C.4 two-call reporter with citations

Label: `contracts-change` (`domain/evidence.py`: `document_citation`; `LLMResponse.citations`;
the knowledge tools' input schema gains `classifications`). No live model call (D15).

- **Breaking:** the knowledge query is built by `ceap.rag.query.retrieval_query` from the parsed
  question (domain vocabulary only), symbol, window and cause candidates; the planner replaces
  whatever query the model wrote and pins `classifications` to the principal's roles.
  `KnowledgeBase.search` scores `"; "`-separated segments separately and merges them round-robin.
- **Breaking:** `knowledge.search_documents` and `knowledge.get_document` take `classifications`
  (comma-separated) and default to `public,internal`; a new policy rule
  (`knowledge-classification`) denies a request for more than the roles allow. Viewers can no longer
  read `restricted` documents (`04_trading_limits.md`).
- **Breaking:** investigation reports are written in two calls: structured claims
  (prompt **reporter_claims@1**), validated against facts, evidence ids and numbers; then prose with
  citations (prompt **reporter_prose@1**) over one `document` block per retrieved chunk with
  `cache_control` on the last. Each citation's quoted text must equal the chunk sent; verified
  citations become `DOC` evidence with the narrative span, document version and corpus hash, a
  fabricated quote is a blocking `NARRATIVE_UNTRACEABLE`. The narrative ends with the knowledge
  corpus hash. New report fields under `critique.knowledge`.
- `AnthropicLLMClient` returns `content_block_location` citations with their offsets in the joined
  text (`LLMResponse.citations`); the mock and the hand-built cassettes cite verbatim.
- Contract snapshots: `wire_request_claims.json`, `wire_request_prose.json` (new),
  `mcp_tools.json`, `wire_request_planning.json` and the ten investigation cassettes regenerated.


### R3B: agents and agentic AI, within the boundary (PLAN section 2, items 3B.1-3B.9)

No live model call anywhere (D15); no code-writing agent (D13); case memory per D10.

#### Breaking / migration

- **Agents no longer fetch their own data.** `BaseAgent.fetched()` reads what the plan fetched and
  never invokes a tool; `ensure` remains only where arguments depend on fetched data (the risk
  agent's limit check). The harness appends, before the first agent step, every tool call a chosen
  agent declares in `ceap.agents.requirements` (`gov-data-NN` steps, validated and policy-checked
  like the plan). An agent whose data step failed fails with `PlanDataMissingError`.
  Mechanical signature change in `agents/quant.py`, `agents/execution.py` (R2T lane) and
  `agents/alpha.py`, `agents/market.py`, `agents/engineering.py` (R2B lane): `self.ensure(` ->
  `self.fetched(`, no logic change.
- **The critic model can no longer change a confidence.** A dispute counts only when it names an
  alternative deterministic check (`check`: `alternative_baseline`, `different_horizon`, `peer_set`)
  whose result contradicts the finding's cited value; the harness runs the check through the policy
  path (at most 3 per task) and caps an overturned finding at 0.3. `supported=False` without a check
  is recorded (`unbacked_disputes`) and changes nothing. `critique.overall` no longer includes model
  text; it is stored separately as `critique.llm_overall`.
- Prompt changes: **planner@3** (scope within enumerated choices: agents, baseline, ranked
  hypotheses, prior cases; data blocks) and **critic@3** (disputes need a named check).
  `report@1`, `research_planner@2` and `research_report@1` are unchanged.
- contracts change (additive): `HypothesisRow` gains `test`, `evidence_ids`, `rank`, `reason`;
  `domain/agents.py` gains `AgentMessage` (envelope version 1) and `parse_message`;
  `CritiqueAssessment` gains `check`; the plan output schema gains optional `baseline` and
  `hypotheses`; `ApprovalDecision` gains `action`; `QueuedApprovalGateway.decide` gains keyword-only
  `action`.
- Tool evidence records carry `values` (top-level scalar fields of the result) so findings can cite
  `values.<field>`; knowledge results are `DOCUMENT` evidence.
- `POST /approvals/{id}`: a reject or modify decision needs a reason in `comment` (422 otherwise);
  `action: modify` means not approved, changes requested.

#### Added

- 3B.1 planner owns scope within enumerated choices: which agents run, which optional tools, which
  baseline (`task_baseline`, `prior_equal_length`, `prior_double_length`; each distinct choice is
  a trial recorded in `agent_outputs.scope.trials`), which pre-registered hypotheses to rank. A
  baseline outside the set is rejected by the schema, semantic validation and the sanitiser.
  `ensure` call sites fall from 28 to 1.
- 3B.2 `analytics/hypotheses.py`: each hypothesis maps to a pre-registered deterministic test; the
  model only ranks; the harness builds `InvestigationReport.hypotheses` and the report shows
  supported, refuted and untested hypotheses, each with its own evidence.
- 3B.3 critic disputes through alternative deterministic checks (above).
- 3B.4 evidence binding: findings carry `evidence_refs {id, path, value}` (bound by the harness from
  the attribute the agent cited); validation drops a finding whose cited value does not match the
  value at its path (`EVIDENCE_VALUE_MISMATCH`) or whose calculation derives from unknown evidence.
- 3B.5 injection hygiene: the question, tool descriptions, prior cases, evidence (logs, deployment
  text) and retrieved policy text reach a model only inside `<data surface=... id=...>` blocks that
  cannot be closed from inside; threat model section per surface; `tests/adversarial/test_surfaces.py`.
- 3B.6 typed agent messages (`AgentMessage`, drifted versions rejected), one bounded re-plan on a
  tool or required-agent failure with the failure reason, and a per-call context budget with a
  documented truncation order (`ceap.llm.prompts.TRUNCATION_ORDER`).
- 3B.7 case memory (D10): after a human decision the harness appends a compact case record; the
  planner receives the three nearest cases of the same dataset as read-only context; append-only,
  audited, partitioned by dataset, no memory tool; OFF in evaluation (`evaluation_platform()`,
  `case_memory.reads == 0` reported). Investigations accept `signoff: true` (optional human sign-off).
- 3B.8 approval review payload: hypothesis table, evidence links, diff against the pre-trade
  estimate (the baseline window until a pre-trade cost model lands) and the critic's disputes;
  `GET /approvals/{id}`; the reject / modify reason is in the hash-chained audit entry and the case record.
- 3B.9 stdio MCP lifecycle: each connection's `AsyncExitStack` is owned by a dedicated task; one
  reconnect when the child exits (test marked `slow`).

### R3A: LLM layer (PLAN section 2, items 3A.1-3A.8)

Round R3A of the 2026-11-07 plan (LLM layer, offline-provable). No live model call anywhere (D15).

### Breaking / migration

- `ceap.llm.client.extract_json` is deleted: model output is validated against strict schemas
  (`ceap.llm.schemas`) and there is no lenient JSON recovery. Fake clients must return the whole
  reply as one JSON document (or set `LLMResponse.parsed`).
- `AnthropicLLMClient` sends the system prompt as a text block with `cache_control`, marks every
  tool `strict: true`, and uses `messages.parse()` when `LLMRequest.output_model` is set. Without
  an explicit model each purpose now uses its own pinned model (previously the narrative model).
- contracts change (`domain/reports.py`, additive): `LLMCallRecord` gains `task_id`, `purpose`,
  `prompt_id`, `prompt_version`, `stop_reason`, `usd_is_estimate`, `redacted_request`;
  `InvestigationReport` gains `prompts`.
- `LLMRequest` gains `output_model`; `LLMResponse` gains `parsed`, `parse_errors`,
  `cache_read_tokens`, `cache_write_tokens`, `request_id`.
- Prompt changes: planner@2, research_planner@2 and critic@2 describe the schema-enforced reply
  instead of a hand-written JSON shape; report@1 and research_report@1 are unchanged in text and
  now live in `llm/prompts/*.md`.

### Added

- 3A.1 structured outputs for planner and critic: plan steps are a union over the read-only tool
  catalogue generated from MCP input schemas; semantic validation (windows, symbol, universe,
  agents, governance duplicates, step count, finding ids, confidence range) is the target of
  exactly one repair, then `DEGRADED`; schema-subset lint. The planner sanitiser now also drops
  tool steps whose window is not a task window.
- 3A.2 wire contract tests over the real `AsyncAnthropic` with an in-memory transport, a committed
  request snapshot (`tests/contract/snapshots/`) and full-format responses validated by the pinned
  SDK types.
- 3A.3 nine adversarial fake clients (`tests/fakes/clients.py`).
- 3A.4 ten harness invariants as property tests under every fake client (100 examples on PR,
  1000 nightly). The narrative-audit invariant becomes blocking when the R3A engine patch lands.
- 3A.5 provenance-tagged cassettes (`ceap.llm.recording`, `tests/cassettes/`): `STALE` replay
  through the real SDK, scrubber on write, `assert_publishable` refuses model-behaviour metrics
  from `hand_built` cassettes; `llm_provenance` labels evaluation output `NOT RUN LIVE`.
- 3A.6 per-call `LLMCallRecord` with a redacted body, per-task cost spans attributed through a
  context variable (`task_scope`, `task_cost`), `CEAP_TASK_BUDGET_USD` enforced before the call
  from a labelled local token estimate, pinned list prices in `model_pins.toml`.
- 3A.7 prompt registry (`llm/prompts/*.md`, sha256 of canonical bytes, version and hash in
  request metadata, call records and reports; golden hash snapshot plus CHANGELOG check).
- 3A.8 adapter equivalence on the ten scenario templates (mock path vs adapter-plus-cassette
  path, structural diff empty apart from narrative). Adapter equivalence, not model fidelity.

## [0.5.0] - 2026-11-08

Rounds R4, R2T and the rf-data series of the 2026-11-07 plan, shipped as one combined PR (AshJha0/AgenticAICashEquities#2).
CI green on the combined head f141320: run 42458999143 (all 9 jobs).

- `.gitleaks.toml`: allowlist the documented `dev-auditor-key` demo key added with the R4 `auditor` role.

### R4: platform and persistence (PLAN section 2, items 4.1-4.9)

#### Breaking / migration
- **Database file.** Tasks, results, approvals, staged orders, LLM call records, the trial ledger,
  idempotency keys and a hash-chained audit log now live in SQLite (`CEAP_DB_PATH`). Default:
  `:memory:` when `CEAP_ENV=dev` (tests and demos leave no file), `var/ceap.db` everywhere else.
  The schema migrates forward on start; a file at an older version is first copied to
  `<db>.v<N>.bak` with the online backup API; a file newer than the code refuses to open. Mount
  `var/` on a volume in containers. New commands: `ceap db status|migrate|backup|verify-chain|prune`.
- **New `auditor` role** (`tasks:read-any`, `metrics:read`, read capabilities only). Reads are now
  scoped to the owner: a viewer (or any role without `tasks:read-any`) gets 404 for another
  principal's task, result, report or trace, and lists only its own. `auditor`, `approver` and
  `admin` read everything. Dev key `dev-auditor-key` added.
- **`Idempotency-Key` header** on `POST /investigations` and `POST /research`: scoped per
  principal, persisted, 24 h TTL; the same key with the same body returns the first response
  (same `task_id`), with a different body 422.
- **`/health` is liveness only** (`status`, `version`). The previous fields moved to the
  authenticated `GET /status` (`tools:read`). **`/metrics` needs a key** with `metrics:read`
  (engineer, auditor, admin).
- **Metric names in the exposition**: counters always end in `_total`; durations recorded in ms
  are exported in seconds (`ceap_tool_latency_ms` -> `ceap_tool_latency_seconds`), and a summary's
  maximum is a separate gauge `*_seconds_max`. Update dashboards.
- **Request limits**: body over `CEAP_MAX_BODY_BYTES` (64 KiB) -> 413; `gross_notional` above 10 bn
  -> 422; MCP `paginate` clamps `limit` to [1, 1000] and `offset` to the list.
- `domain/common.py` (contracts-change): `utc_now()` and `new_id()` honour an injected clock / id
  source held in context variables (`CLOCK`, `ID_SOURCE`); unset, behaviour is unchanged.

#### Added
- 4.1 `ceap.persistence`: stdlib `sqlite3` behind one writer thread (`Database.run` / `arun`),
  WAL, `busy_timeout`, `PRAGMA user_version` forward-only migrations (`migrations/0001_initial.sql`,
  `0002_platform.sql`), backup before migrate, audit hash chain over canonical bytes
  (`ceap db verify-chain`), retention for results, traces and raw model I/O
  (`CEAP_RETENTION_*_DAYS`) and a size bound (`CEAP_DB_MAX_BYTES`, oldest results pruned first; the
  audit log is never pruned).
- 4.2 Restart semantics: on start, tasks left `PENDING`/`RUNNING` become `INTERRUPTED` and pending
  approvals `EXPIRED` (audited). A decision is a conditional `UPDATE ... WHERE status='PENDING'`:
  of two concurrent decides exactly one wins; the loser and any late decide get 409 and an audit row.
- 4.4 A task's concurrency slot is released while it waits for a human, so four tasks awaiting
  approval no longer block a fifth; `CEAP_APPROVAL_TIMEOUT_S` (default 120).
- 4.5 The staging approval carries `portfolio_hash` of the portfolio the approver saw; the harness
  passes it to `execution.stage_orders` (`approved_portfolio_hash`), which recomputes the portfolio
  and refuses on a mismatch (TOCTOU closed).
- 4.6 API keys held as salted HMAC-SHA256 digests; `CEAP_API_KEY_HASHES` takes pre-hashed keys with
  optional expiry (`sha256$salt$digest:role[:YYYY-MM-DD]`); raw keys never in reprs, logs or errors;
  `X-Request-ID` correlation id on every response and every log line; one access-log line per request.
- 4.7 Task replay bundles: `ceap replay BUNDLE --record --dataset T01` writes inputs, tool outputs,
  redacted model I/O, prompt hashes, approval decisions and the clock seed; `ceap replay BUNDLE`
  re-runs offline under an injected clock and checks the result byte for byte (exit 1 on drift,
  naming the first differing path).
- 4.8 Prometheus exposition passes `promtool check metrics` rules (HELP/TYPE, `_total`, base units);
  HTTP metrics `ceap_http_requests_total`, `ceap_http_errors_total`,
  `ceap_http_request_duration_seconds`; an OpenTelemetry span test.
- 4.9 `tests/properties` (hypothesis: VWAP within [low, high], IS components sum, fully-filled IS =
  execution cost, adverse cost >= 0, impact non-negative and monotone in participation, weights sum
  to 1 and dollar-neutral; 100 derandomised examples on PR, 1000 nightly), `tests/contract` (MCP
  schema contract + committed catalogue snapshot), `tests/golden` (research report goldens under the
  replay clock; TCA goldens are regenerated at 0.5.0 integration after the R2T re-baseline).

#### Fixed
- `Settings` repr no longer prints `api_keys` or `ANTHROPIC_API_KEY`; the duplicate-key error no
  longer echoes the key.
- Span start times are late-bound to `time.perf_counter`, so an injected clock reaches them.

### R2T - TCA realism (2T.1-2T.8)

- Session clock is US regular hours (09:30-16:00 America/New_York, `ceap/data/session.py`) with a
  U-shaped intraday profile; baselines are the same clock window on the 5 previous sessions.
- Perold IS with delay, spread, timing, opportunity and fee legs (sum to total to 1e-9); IS
  standard error from the schedule; deterioration is residual z >= 1.96 against a pre-trade
  estimate (parametric square-root model with split-conformal 90 % intervals, `ceap/ml`).
- Size-weighted effective/realised spreads, price improvement, markouts (1/10/60/300 s).
- Peer-relative effects with bootstrap CI (no causal claim); multi-day TCA universe, broker/algo
  comparison, four-eyes outlier queue and `ceap tca-report`.
- **contracts-change:** `Order` gains `decision_time`, `adv_shares`, `sigma_daily_bps`, `broker`;
  `ExecutionMetrics` gains IS components, pre-trade and markout fields (all defaulted);
  `VenueStatistics.volume_share` renamed `market_volume_participation` (alias kept);
  `Trade.condition` added.
- Approved tz edits outside this lane: `platform.py` and `api/parsing.py` take the session tz from
  `ceap.data.session`; `agents/reporter.py` report title says New York.

Re-baseline (50 synthetic scenarios, mock LLM):

| Metric | 0.4.0 | R2T |
|---|---|---|
| Primary-cause accuracy | 1.00 | 0.98 |
| Cause coverage | 1.00 | 0.98 |
| False-positive rate | 0.02 | 0.06 |
| Completed | 1.00 | 1.00 |
| Number-audit warnings | 0 | 0 |
| T01 benign market impact | about -45 bps | 2.3 bps (mean, 200 seeds) |
| T01 false-flag rate | n/a | 0.01 (200 seeds) |
| Broker 3 bps gap power | n/a | 0.83 (100 seeds) |
| Broker test size (no gap) | n/a | 0.05 (100 seeds) |

The scenario numbers fall slightly because the generator is harder (U-shape, fees, delay, odd
lots and late prints) and attribution now needs a statistically significant residual.

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

- **Risk-free series committed** (`data/reference/rf.csv`, Ken French daily RF, 2000-01-03 to
  2026-09-30, attribution header). The null sweep was re-run with it: the hurdle moved from
  t = 0.65 (rf = 0, hash `dfdadabe...`) to **t = 0.40** (hash `ef474869...`), because long-only noise
  configurations now pay rf and clear the Sharpe gates less often.
- **Working orders.** A fill clipped by the 10 %-of-ADV cap now leaves a working order that is
  traded on every following session at the cap until it completes or the next decision replaces
  it (`working_order` events). Before, the residual waited for the next rebalance, so at 250-500 M
  the book silently under-traded and its Sharpe flattened: with rf the 250 M and 500 M Sharpe
  ratios tied within 0.002 (Spearman -0.943 against the plan's -1). The acceptance was not
  changed; with the fix R03 is strictly monotone again (0.63 at 10 M to -0.84 at 500 M, gap 1.46
  against two standard errors of 0.46), as are R01, R08 and R09.
- **Installed wheel.** `data/` is not shipped: rf falls back to 0 (stated), the NYSE calendar is
  regenerated from its rules, and the hurdle falls back to the committed value in code (a test
  keeps it equal to the thresholds file).

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
  hurdle **t = 0.40** (15/500 false PROMOTEs, 3.0 %, band [1.7 %, 4.9 %]), sweep hash
  `ef474869e5fa503d593b460c7e8921a7d26f106d483a2c5c6c2708dc1faf57d4`, in
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
