# Reviewer Q: quant research and statistics (CEAP v0.9.0, 2026-11-12)

## 1. Scope covered, and what I did not get to

Read line by line: `analytics/signal_statistics.py`, `overfitting.py`, `research_assessment.py`
(gate chain, deflated Sharpe, Sharpe interval), `trial_accounting.py`, `trial_ledger.py`,
`null_sweep.py` (to line 200), `power.py`, `adversarial.py`, `backtest.py` (all), `risk_research.py`,
`portfolio_risk.py`, `ml_signals.py`, `signals.py`, `studies/industry_momentum.py` (all),
`data/real/prereg.py`, `ml/registry.py` (hashing, PSI, load), the regression part of
`attribution.py`, parts of `profitability.py`, `calibration.py`, `ml/anomaly.py` (threshold tuning),
`scripts/run_e1_industry_momentum.py`, `rebuild_study.py`, `fetch_kf_industries.py`,
`train_ml_models.py` (detector and calibration). Docs and results: E1 prereg, lock, ledger, data
hashes, both result JSON files, `e1/README.md`, `docs/research/README.md`, `quant-errata-0.9.0.md`,
the statistics and evaluation sections of `quant.md`, `quant_power_curve.json`,
`adversarial_power.json`, `null_sweep_per_seed.json`, `research_thresholds.json`, `docs/ml/model_cards.md`.

Ran (repo has no `.venv`; used the main checkout's venv with `PYTHONPATH=src`, no files written):
prereg hash recomputation; Lo standard error in three implementations; expected-max-Sharpe against
a 200k-draw Monte Carlo; deflated Sharpe under different ledger variances; `factor_regression` on
constant y; `raw_betas_ex_self` on a flat market; `ledoit_wolf` against scikit-learn; `pbo_cscv` on
60 pure-noise matrices; Newey-West size on AR(1) series; `generator_hash()` against the registry.

NOT covered (be aware): the generators themselves (`data/synthetic.py`, `historical.py`,
`research_scenarios.py` internals, `ground_truth.py` closed forms were read only in the docstring),
`data/real/` (momentum, universe_pit, prices, quality, vintage), `calendar.py`, `peers.py`,
`liquidity.py`, `volatility.py`, `market_statistics.py`, `alpha_decay.py`, `factor_check.py`,
`leakage.py` internals, the CUSUM/PELT maths in `anomaly.py`, `ml/inventory.py`,
`r5v2_insample_checks.py`, `fetch_ff_factors.py`/`fetch_rf.py`/`fetch_sp500_changes.py`,
`real-data-and-paper.md`, the R5 result pages, `replication_report.json`, `e2_why_r5_could_not_pass.md`.
I did not audit the tests (no pytest run) and did not check the golden files. D5, D10, D14, D15 are
therefore "not verified" below, not "fine".

## 2. Score: 6 / 10 against a top-firm bar

- The honest parts are real: E1 is reported as NULL with no spin, the prereg hash reproduces, R5 is
  VOID twice and says so, synthetic results are labelled synthetic, stylised premia state their
  multiple.
- Core formulas I checked are mostly right (expected max Sharpe, DSR denominator with raw kurtosis,
  excess-of-rf Sharpe, Ledoit-Wolf exact to 4e-16 against scikit-learn, masked Newey-West, purged
  walk-forward).
- But about half of the 0.8.0 "research realism toolkit" (PBO, effective trials, Ledoit-Wolf,
  factor regression, residualisation, turnover-penalised weights, tail report, robustness report)
  is library code that no agent, MCP tool, CLI command or script calls. It is tested and unused.
- One Sharpe standard error is wrong and the errata repeats it; the deflated Sharpe gate can be
  lowered by the ledger's own variance; the published power curve is for a hurdle no longer in
  force; the adversarial study is 8 seeds; the pinned RAG corpus serves the stale hurdle.
- E1 is a careful exercise, but a "holdout" on the most studied public return file in finance,
  opened 24 minutes after the lock, with raw data not archived, is weaker than its wording.

## 3. What is good (verified)

- E1 prereg hash: recomputed SHA-256 (CRLF to LF) = `06dac8d3...74fa2`, equal to
  `docs/research/e1/prereg_lock.json:3`. Git order prereg `2631118` (17:42) -> code and data hashes
  `f10c631` (18:02) -> in-sample `df4757d` (18:05) -> ledger `a04bebc` (18:11) -> result `7d5aebf`
  (18:29) matches `docs/research/e1/README.md:16-24`. The crash and re-run are ledgered
  (`holdout_ledger.json`), not hidden.
- E1 code matches the prereg: 6-month formation, no skip, top/bottom 7, equal weight in leg,
  value-weighted first block, drifted-weight turnover, 10/20 bps, Andrews lag, 1.645
  (`src/ceap/studies/industry_momentum.py:48-53,137-173,198-205,420`). Holdout t recomputed by hand
  from mean and Sharpe: iid t 1.19, NW 1.16. Verdict line in the README is the allowed NULL claim.
- Data hashes are verified at run time before anything is parsed (`industry_momentum.py:84-92,392`).
- Expected maximum Sharpe (`research_assessment.py:142-149`): N=24, unit variance gives 1.980;
  Monte Carlo 1.948 (the Bailey-Lopez de Prado approximation, slightly conservative). Kurtosis is
  raw (normal = 3) at both ends (`backtest.py:222`, `research_assessment.py:170-171`): consistent.
- Sharpe and Sortino are on returns in excess of rf (`backtest.py:201-227`); skew/kurtosis on the
  same excess series.
- Backtest timing: decision at close t, fill at t+lag (default 1), costs from drifted weights,
  participation and weight caps logged, residual worked on later sessions, delisting bounds
  reported both ways (`backtest.py:470-473,484-537,638-648`).
- IC gate uses non-overlapping horizon ICs starting after the execution lag, and trims the last
  `L + h` in-sample dates (`signal_statistics.py:241,307-312`).
- `ledoit_wolf` (`risk_research.py:36-51`): shrinkage 0.22753 against scikit-learn 0.22753, max
  element difference 4e-16.
- Ridge CV: purge `t + h + embargo < test[0]`, walk-forward only, labels end inside in-sample
  (`ml_signals.py:108-127`).
- Registry: parameters are re-hashed on load and a mismatch raises (`ml/registry.py:228-242`).
- `docs/research/README.md:181-183` states openly that the IC gate "detects" a premium planted on
  market beta (5/8 and 8/8 false positives). That is the right thing to publish.

## 4. Findings

### Q-01 [P1] Sharpe confidence interval uses the annual-data form of Lo (2002) on daily data; the errata repeats it
- Evidence: `src/ceap/analytics/research_assessment.py:121-122` (`se = math.sqrt((1.0 + sharpe_ann**2 / 2.0) / years)`);
  `docs/quant/quant-errata-0.9.0.md:44-48`; correct versions in the same package at
  `src/ceap/analytics/profitability.py:33-37` and `src/ceap/paper/forward.py:40-43`.
- Status: VERIFIED (ran both: 5 years, SR 0.3 -> 0.4572 vs 0.4473; SR 1.0 -> 0.548 vs 0.448; SR 2.0 -> 0.775 vs 0.449).
- Confidence: high
- Why it matters at a top firm: Lo's `sqrt((1 + SR^2/2)/T)` is per period. Annualising gives
  `sqrt((1 + SR_ann^2/(2*252)) / years)`. The code drops the 252, so the interval is too wide by 22 %
  at Sharpe 1 and 73 % at Sharpe 2. This interval decides `INCONCLUSIVE` vs `REJECT`
  (`research_assessment.py:287-290`), so a verdict depends on it. Three implementations of one
  quantity, one wrong, and the errata's worked example (SE 0.457, interval [-0.60, 1.20]; correct
  0.447, [-0.58, 1.18]) cites Lo for a formula Lo did not write. A reader who knows the paper sees it at once.
- Fix: delete `sharpe_interval`'s own formula and call `profitability.sharpe_standard_error`;
  correct errata item 2. Acceptance: one test asserting the three functions agree to 1e-12 on
  (SR, T) in {0.3, 1, 2} x {252, 1260, 3528}, and a golden re-run showing which verdicts move.
  Better: add the Mertens (2002) skew/kurtosis term, since the DSR already uses it.

### Q-02 [P1] Deflated Sharpe gate: ledger variance is not floored, the null sweep does not test the production path, and the bar is 0.5
- Evidence: `src/ceap/analytics/research_assessment.py:164-166` (ledger variance replaces `1/T`
  whenever it is > 0), `:91` (`dsr_min = 0.5`), `:219-224` (production passes the ledger variance);
  `src/ceap/analytics/null_sweep.py:128` (sweep passes `None`); `src/ceap/mcp/backtest/server.py:33`
  (ledger records the **net** in-sample Sharpe, the DSR deflates the **gross** one).
- Status: VERIFIED (ran `deflated_sharpe(0.3, 2520, 0, 3, 24, var)`: null variance -> DSR 0.15, bar 0.63; var 1e-6 -> DSR 0.80, bar 0.03; var 1e-8 -> 0.83, bar 0.003).
- Confidence: high on the mechanics; medium on how often it bites in the shipped flows
- Why it matters at a top firm: the count is floored at 24 but the variance is not floored. A
  ledger holding near-duplicate trials (same configuration at two cost multipliers or two start
  dates, both allowed and both counted) has a tiny cross-trial variance, and `SR0` collapses.
  The false-PROMOTE rate published for the gate (0/500) was measured with the null variance, so it
  does not cover the variance the product actually uses. The ledger also mixes windows of
  different length and records net while deflating gross. Finally 0.5 only asks that the Sharpe
  beat the expected maximum; Bailey and Lopez de Prado use 0.95. The plan called this "a correct
  deflated Sharpe"; the formula is, the inputs are not.
- Fix: `var = max(ledger_var, 1/T)` (or always the null value until effective-N is wired); record
  the gross in-sample per-day Sharpe on a fixed window; make the sweep call the same code path as
  `assess_research`; state why 0.5 and report what 0.95 would do. Acceptance: a property test that
  adding a duplicate-like trial never lowers `SR0`; null sweep re-run through `assess_research`.

### Q-03 [P1] The research realism toolkit is mostly unreachable code
- Evidence: no caller outside tests for `pbo_cscv` (`overfitting.py:41`), `mean_ci` (`:125`),
  `effective_trials_eigen`/`_cluster`/`trial_correlation`/`per_period_sharpe_variance`
  (`trial_accounting.py:31,42,61,84`), and everything in `risk_research.py` (`ledoit_wolf:36`,
  `factor_regression:55`, `residualise:89`, `multi_factor_stress:103`, `tail_report:119`,
  `regime_split:137`, `turnover_penalised_weights:158`, `cost_frontier:168`, `robustness_report:194`).
  `n_trials_effective` and `preregistered_variants` are read at `research_assessment.py:105-106`
  and written nowhere. `ModelRegistry.check_drift` (`ml/registry.py:248`) has no caller.
- Status: VERIFIED (grep over `src` and `scripts` for every name and for module imports).
- Confidence: high
- Why it matters at a top firm: the CHANGELOG and `docs/research/README.md:185-197` list D6, D8,
  D9, D13, D16, D17, D18, D19 as delivered. What was delivered is functions with unit tests. No
  PBO number exists for any trial matrix in the repo, no covariance enters any risk number (the
  risk report is exposures, beta, HHI and linear shocks), no result is residualised on factors, no
  verdict carries a robustness report. A reviewer who asks "show me the PBO of your 24-configuration
  search" gets nothing.
- Fix: either wire them (PBO and effective N from the 24 trial return series into the backtest
  tool's trial record; `factor_regression` and `residualise` into the E1 and R08 reports;
  `robustness_report` attached to each verdict) or relabel them "library, not used by the pipeline"
  in the docs. Acceptance: `ceap evaluate --suite research` output contains `pbo`, `n_trials_effective`
  and a robustness block, with committed values.

### Q-04 [P1] The pinned RAG corpus serves quant facts the project knows are wrong
- Evidence: `data/reference/knowledge/manifest.json:4` (quant.md in corpus, "version 0.4.0");
  `docs/quant/quant.md:17,144-145,171-190,196,267-277,288-290`; `docs/quant/quant-errata-0.9.0.md:8`
  ("This page is kept out of the RAG corpus").
- Status: VERIFIED (read both; did not run the retriever)
- Confidence: high that the text is stale; medium on user impact (depends on what is retrieved)
- Why it matters at a top firm: the errata frames this as a docs lag. It is also a product defect:
  any agent answer grounded on quant.md will say the hurdle is 0.40 and "measured, not chosen",
  that there are two verdicts, and that the suite scores 29/30. The CHANGELOG discloses the
  errata, not that retrieval is knowingly serving superseded statements. Worse than disclosed.
- Fix: at the 0.10.0 re-measurement fold everything in section "Errata" below into quant.md; until
  then add the errata file to the corpus with higher priority, or mark quant.md chunks for those
  sections as superseded. Acceptance: a retrieval test asking "what is the PROMOTE hurdle" returns 3.0.

### Q-05 [P1] No power figure exists for the hurdle in force, and the closed form says it is near zero at realistic premia
- Evidence: `docs/quant_power_curve.json` (`"hurdle": 0.4`); `src/ceap/analytics/power.py:46`
  (reads the current threshold, so the file is simply not regenerated);
  `docs/research/adversarial_power.json` (8 seeds).
- Status: VERIFIED (file contents); the power numbers below are my arithmetic from the file's own expected t values, not a simulation.
- Confidence: high
- Why it matters at a top firm: the errata (item 3) says the file is at 0.40. Left unsaid: with the
  file's expected t (IS 1.98, OOS 1.17 at 1200 bps, gross Sharpe 0.50) the IC gates at 3.0 pass
  about `Phi(1.98-3) * Phi(1.17-1)` = 9 % of the time, and about 4 % at the "realistic" 1100 bps.
  So the default pipeline essentially cannot PROMOTE a signal with a real-world Sharpe on 14 years;
  it can only return INCONCLUSIVE. That is a defensible design, but it must be stated as the
  headline property of the gate, with a measured curve.
- Fix: regenerate at 3.0 and 1.645 with at least 200 seeds a point and Clopper-Pearson bands;
  put the "minimum premium detected with 80 % power" in quant.md. Acceptance: `power.py` output
  committed with `hurdle: 3.0`; a test that the file's hurdle equals `DEFAULT_THRESHOLDS.ic_t_stat_min`.

### Q-06 [P1] Known false-positive family (premium on market beta) is unmitigated in the gate
- Evidence: `docs/research/README.md:179-183` (5/8 at 1100 bps, 8/8 at 2400 bps counted as false
  positives at t >= 3); `src/ceap/analytics/risk_research.py:89` (`residualise`, never called);
  `src/ceap/analytics/research_assessment.py:229-268` (alpha chain has no factor-neutral test).
- Status: VERIFIED for the IC gate; SUSPECTED for the full verdict (the book-beta limit 0.5 at
  `portfolio_risk.py:58` may flag some of these as LIMIT_BREACH; the study measures only the IC part).
- Confidence: medium
- Why it matters at a top firm: "is it alpha or beta" is the first question asked of any signal.
  The project measured that its gate fails it and wrote the remedy, then stopped.
- Fix: gate on the IC of the beta- (and industry-) residualised signal, or on alpha t from a market
  regression of the book; run the `market_corr` family through the full `assess_research`.
  Acceptance: `market_corr` false-PROMOTE rate at or below 5 % with a 200-seed band.

### Q-07 [P2] Adversarial study: 8 seeds a point, shared null datasets, same author
- Evidence: `src/ceap/analytics/adversarial.py:44` (`seed=70_000 + seed` for every family), `:93`
  (`seeds: int = 8`); `adversarial_power.json` (drift, hidden_factor and regime_switch at premium 0
  all show `mean_t_is` 0.3352: the same eight datasets).
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: 0/8 has a 95 % upper bound of 37 %. Three of the five "false
  positive rate" rows are one experiment counted three times. The README does say "a coarse first
  look"; the module docstring and plan item D2 ("written blind to the detector") claim more than
  one author can deliver.
- Fix: 200+ seeds, family-specific seeds, bands in the table; drop "blind" or have the families
  written by someone who has not seen `signals.py`. Acceptance: JSON carries `ci95` per row.

### Q-08 [P2] E1 is a pseudo-holdout on public, heavily studied data; evidence of the lock is local git time only
- Evidence: `docs/research/e1/prereg_lock.json:5-7`; `docs/research/e1/holdout_ledger.json:3`;
  prereg line "before any industry or factor return used by this study was downloaded";
  commit times 17:42 (lock) and 18:06 (opening).
- Status: JUDGEMENT
- Confidence: high
- Why it matters at a top firm: nobody designs a 1990-2015 industry-momentum test blind. The
  literature (and the model that signed the prereg) knows momentum weakened after the 1990s and
  crashed in 2009. "Data not downloaded" is true and beside the point. The 24-minute gap, the
  self-referential lock (file and hash in the same repo) and local commit dates give no external
  timestamp. The only genuinely unseen data is the live window after 2026-09. None of this changes
  the NULL, and a NULL is the outcome least open to this criticism, but the page should say it.
- Fix: add a "limits of this preregistration" paragraph to `e1/README.md`; push tags or use an
  external timestamp for the next lock; pre-commit the live-window test (2026-10 onward) now, with
  a fixed review date. Acceptance: text present; next prereg has a third-party timestamp.

### Q-09 [P2] E1 cannot be rebuilt from the repository, and the fetch script overwrites the recorded hashes
- Evidence: `.gitignore:45` (`data/real_cache/`); `scripts/fetch_kf_industries.py:24-28` and
  `src/ceap/studies/industry_momentum.py:72-81` (`record_hashes` rewrites `data_hashes.json`);
  `scripts/rebuild_study.py` (checks the prereg hash only; never runs the study);
  `industry_momentum.py:427-436` (ETF gate downloads yfinance data at run time, unhashed).
- Status: VERIFIED (read; no download attempted)
- Confidence: high
- Why it matters at a top firm: Ken French revises his files. After the next revision nobody,
  including the owner on a new machine, can reproduce t = 1.16; running the documented fetch
  command silently replaces the hashes the result was bound to. The ETF gate (a gate that can VOID
  the study) rests on a vendor call that returns different bytes over time. Plan item D20 promised
  "a uniform per-study rebuild script (prereg plus data hashes)"; it rebuilds nothing.
- Fix: commit the four zips (about 1 MB; the library is free to redistribute with attribution; if
  the owner prefers not, commit the parsed monthly CSVs) and the ETF monthly closes used; make the
  fetch script refuse to overwrite an existing `data_hashes.json` without `--new-vintage`; make
  `rebuild_study.py` re-run in-sample and compare to `result_in_sample.json`. Acceptance: fresh
  clone, no network, `rebuild_study.py docs/research/e1/prereg_lock.json` reproduces the in-sample JSON.

### Q-10 [P2] Registered models are stale against the generator and the model card header is wrong
- Evidence: `docs/ml/model_cards.md:3-4` ("current generator hash is `d3074158ce168b03`"); cards in
  `data/models/*.json` say `ce0da55d75b78559`; `ml/registry.py:228-242` (load never compares
  generator hashes).
- Status: VERIFIED (ran `generator_hash()` on v0.9.0: `7f59073801a673bc`; three different values)
- Confidence: high that hashes differ; low on whether the generator's behaviour for these models changed
- Why it matters at a top firm: "fitted to synthetic generator <hash>" is the project's own
  provenance label. The detector threshold (h = 11, 0/300 false alarms), the isotonic map and the
  ridge card were fitted on 2026-11-08; the generator sources changed on 11-09 (D1, D5). The card
  metrics describe a generator that no longer exists and nothing fails.
- Fix: a contract test that every registered card's `generator_hash` equals the current one (or an
  allow-list with a reason); regenerate models and cards at release. Hash only the parts of the
  generator each model depends on, so a docstring edit does not force a refit.

### Q-11 [P2] Ridge combination is scored at lag 0, the convention the backtest calls look-ahead
- Evidence: `src/ceap/analytics/ml_signals.py:11` (label `close[t+h]/close[t] - 1`), `:177`
  (returns from `t + 1` for weights formed at `t`); `src/ceap/analytics/backtest.py:9-10`
  ("`lag=0` with a close fill is the 0.3.x same-close look-ahead"); `docs/ml/model_cards.md:60-66`
  (OOS Sharpe 2.22, `promote: True`).
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: two execution conventions in one platform. The ridge includes a
  5-day reversal feature, the most lag-sensitive signal there is, and its promote flag is computed
  on a book the main backtest would refuse to call tradable, gross of costs.
- Fix: add `execution_lag_days` to the panel (label and returns from `t + 1 + lag`), default 1,
  and score net of the backtest cost model. Acceptance: the leakage canary run through
  `portfolio_returns` collapses at the same delay as through `simulate_backtest`.

### Q-12 [P2] Trial ledger is per process by default; a restart forgets the search
- Evidence: `src/ceap/analytics/trial_ledger.py:44-51` (file only when `CEAP_TRIAL_LEDGER` is set);
  `src/ceap/mcp/backtest/server.py:41` and `src/ceap/mcp/alpha/server.py:21` (each server builds its
  own `TrialLedger()` unless one is injected).
- Status: VERIFIED (read)
- Confidence: medium (I did not trace how the servers are constructed in the default app)
- Why it matters at a top firm: the floor of 24 saves the default flow, but the tool accepts free
  `start`, `end`, `in_sample_end`, `cost_multiplier`, lag and fill, so a search can exceed 24, and
  the count returns to the floor on restart. "The trial count is honest and enforced" holds only
  inside one process.
- Fix: persistent ledger by default under the data directory; one shared instance for both servers;
  count window changes as trials against the dataset. Acceptance: integration test that restarts
  the server and still sees the earlier trials.

### Q-13 [P2] PBO docstring gives a single-run reading the estimator cannot support
- Evidence: `src/ceap/analytics/overfitting.py:10-11` ("a PBO of 0.5 is what pure noise produces,
  0.1 means the in-sample winner is usually a genuine out-of-sample winner").
- Status: VERIFIED (ran 30 pure-noise 800 x 20 matrices at S = 8: mean 0.47, range 0.07 to 0.96; N = 4: 0.01 to 0.94)
- Confidence: high
- Why it matters at a top firm: the estimator is right in expectation, but the splits share the
  same data, so one matrix of pure noise returns 0.07 as readily as 0.9. Anyone who wires this in
  and reads "0.1 = genuine" will promote noise.
- Fix: return a bootstrap interval (stationary bootstrap of rows) with the point value and rewrite
  the docstring. Acceptance: test that the 90 % interval on noise covers 0.5 in at least 80 % of seeds.

### Q-14 [P2] Exact-zero guards: the two disclosed ones, plus three more of the same kind
- Evidence: disclosed: `src/ceap/analytics/risk_research.py:85` (`if tss > 0`),
  `src/ceap/analytics/portfolio_risk.py:87` (`if sxx <= 0`). Same pattern, not disclosed:
  `src/ceap/analytics/profitability.py:122-126` (`sxx > 0` in `factor_alpha`),
  `src/ceap/analytics/ml_signals.py:188` (`r.std(ddof=1) == 0.0`),
  `src/ceap/analytics/attribution.py:631` (`sd > 0`).
- Status: VERIFIED for the first two (constant y of 0.3, 0.1, 1.0, 0.7 gave R² 0.0, 0.958, NaN, 1.0;
  a flat ex-self market gave beta -1.36e15 with SE 4.2e14); read only for the other three.
- Confidence: high
- Why it matters at a top firm: an R² of 0.96 for a constant series in a report is the kind of
  number that ends a meeting.
- Fix, and the right tolerance: the rounding residue of `sum((y - mean)^2)` for a constant series
  is about `n * (eps * max|y|)^2`, and the relative error of TSS is about `2 * eps * max|y| / sd`.
  So declare the statistic undefined when `sd(y) <= 1e-9 * max|y|`, that is
  `tss <= n * (1e-9 * max(|y|))**2`, plus an absolute floor for return series
  (`tss <= n * 1e-24`, sd below 1e-12) to catch an already-demeaned constant. Same rule for `sxx`
  with `x`. The errata's proposal `1e-12 * n * mean(y)^2` is the right size but uses the mean, so it
  is zero, and useless, for a series centred on zero; use `max|y|`. One shared helper
  `is_degenerate(v)` for all five sites. Acceptance: property test over constants 1e-6 to 1e6 and
  n 10 to 5000: R² is NaN, beta is the prior and SE is inf.

### Q-15 [P3] Two Andrews/Newey-West implementations with different conventions
- Evidence: `src/ceap/analytics/signal_statistics.py:159-203` (weights `1 - k/(bw + 1)` with
  non-integer `bw`, lag `floor(bw)`); `src/ceap/studies/industry_momentum.py:198-224` (integer lag,
  weights `1 - j/(lag + 1)`, rho with a different denominator, division by zero on constant input: disclosed).
- Status: VERIFIED (read). Size check of the first: AR(1) rho 0.5, n 250: 8.3 % at nominal 5 %; rho 0.9: 17.8 %.
- Confidence: high
- Why it matters: the over-rejection is the known HAC small-sample behaviour, not a bug, but the
  docs should say that NW t at 1.645 is liberal for persistent series; E1's lag was 1, so E1 is not affected.
- Fix: one implementation; note the size distortion; E1's version is frozen by the prereg, so keep
  it as is and test that the shared one reproduces its numbers.

### Q-16 [P3] Small items
- `industry_momentum.py:153`: eligibility requires the *next* month's return to exist. Registered
  in the prereg (section 2) and immaterial in this file (industries start late, do not stop), but
  it is conditioning on the future; do not copy it to a stock-level study.
- `signal_statistics.py:90-98` and `ml_signals.py:159`: ordinal ranks break ties by column order.
  With clipped z-scores (ties at +/-3) or a degenerate cross-section (`signals.py:120-128` returns
  all zeros and `backtest.py:145` then longs the last columns and shorts the first) the result
  depends on symbol order. Use average ranks; return no trade on a degenerate cross-section.
- `backtest.py:310-313,465-467`: on an index exit the name's actual return that day is replaced by 0.
- `backtest.py:512,527`: for an open fill, `vol_bps[e]` already contains that day's close (cost side only).
- `docs/research/README.md:192`: points to "known numerical issues in quant.md"; they are in the errata.
- `calibration.py:131` with `train_ml_models.py:99-101`: "shipped" is decided on the same held-out
  set whose Brier score is then published; detector tuning sessions (seeds 0-499) overlap the
  calibration session seeds (`seed * 100 + j`).
- `docs/ml/model_cards.md`: the detector card has in-sample false alarms only (0/300, upper 1.2 %)
  and no held-out detection rate or challenger number, although a challenger is named in the inventory.
- No Bonferroni/Holm/BH anywhere in the research path (grep): fine, since the design is an a-priori
  t >= 3 plus DSR, but quant.md's "Multiple testing" heading should say that no p-value correction is applied.

## 5. Plan 2026-11-09, my area

| Item | Status |
|---|---|
| D1 recalibrate premia | Done and convincing in the docs (labels "Stylised", 4x stated, 11 %/yr SD explained). Generator not audited. |
| D2 adversarial families | Done but weak (Q-07): 8 seeds, shared nulls, not blind. |
| D3 a-priori hurdle, FPR and power | Hurdle and FPR done (0/500 at 3.0 verified in `research_thresholds.json`). Power at 3.0 not done (Q-05). |
| D4 INCONCLUSIVE | Done; interval formula wrong (Q-01). |
| D5 500 names | Not verified. |
| D6 robustness report | Function only, not attached to any verdict (Q-03). |
| D7 leakage canary | Present (`leakage.py:180`), numbers in README plausible; not re-run. |
| D8 trial accounting | Floor done. Effective N and same-horizon variance: functions only, never called (Q-02, Q-03). |
| D9 PBO and block bootstrap | Functions only; no PBO on the trial matrix (Q-03, Q-13). Bootstrap used by E1 (own copy) and the ridge comparison. |
| D10 delisting sensitivity | Not verified. |
| D11 masked NW, Andrews | Done; verified by simulation (Q-15). |
| D12 baseline and DM test | Done (`ml_signals.py:309-321`). Lag-0 issue Q-11. |
| D13 LW, factor regression, stress | LW exact; none of it used; R² guard open (Q-03, Q-14). |
| D14 data vintage | Not verified for R5; E1 fetch overwrites hashes (Q-09). |
| D15 alpha decay | Exists, not called by the pipeline; not verified. |
| D16 factor-neutral research | Function only (Q-06). |
| D17, D18 capacity, cost-aware construction | `capacity_sweep` is wired in the backtest tool; `turnover_penalised_weights` and `cost_frontier` are functions only. |
| D19 tails, regimes | Functions only. |
| D20 rebuild script | Weak: checks a hash, rebuilds nothing (Q-09). Per-seed vector published (without the per-family t values the code produces). |
| D21 sweep ledger | Done (`data/reference/sweep_ledger.jsonl`). |
| D22 model inventory | Done, honest ("validator unassigned"); stale hashes (Q-10). |
| D23 labels | Done (`signal_statistics.py:13-14`, `backtest.py:654`). |
| E1 | Done, honest NULL. Replication gate NOT RUN (stated in advance); Q-08, Q-09. |
| E2 | Present; not read. |

## 6. Area-specific section

### 6.1 Errata: each item checked, and what must go back into quant.md

Folding any of this into quant.md changes its bytes, so the corpus pin, the dense and hybrid
retrieval caches, the preregistered RAG results and the report cassettes all move and must be
re-measured in the same release. Do it once.

| Errata item | Right? | Fold into quant.md |
|---|---|---|
| 1. Hurdle is a priori (3.0 / 1.645) | Yes. 0/500 and upper bound 0.74 % match `research_thresholds.json`; 15/500 at 0.40 matches. | Replace lines 171-190 and the `NO_ALPHA` row at 196. State that the 0.74 % is a two-sided 95 % bound. |
| 2. INCONCLUSIVE | Rule right; **formula and worked example wrong** (Q-01): SE 0.447 not 0.457, interval [-0.58, 1.18]. | Line 17 (three verdicts), verdict section; use the corrected formula. |
| 3. Power table | Numbers match `quant_power_curve.json` exactly. But they are for hurdle 0.40. | Replace 267-277 with a curve at 3.0 and 1.645 (Q-05), not with these figures. |
| 4. Suite score 25/30 | Consistent with the CHANGELOG (0.8333). | Lines 288-290. |
| 5. Numerical issues | Both reproduce (Q-14). The quoted R² range differs from the CHANGELOG's "0.93": both are noise; say "undefined". Proposed tolerance needs `max|y|`, not the mean. | Add after the fix; then delete the note. |

Also stale in quant.md and missing from the errata: line 144-145 (NW "h - 1 lags"; code now takes
the larger of h - 1 and the Andrews bandwidth, `signal_statistics.py:266`); line 148
(`ic_ir_annualised` should carry "descriptive only"); line 168 should say the DSR variance source
and its floor once Q-02 is fixed; the page never mentions E1; the "Multiple testing" section should
name PBO and effective N only if they are wired.

### 6.2 Candidates

| Candidate | Present now | Value here | How to measure against today's baseline | Cost | Verdict |
|---|---|---|---|---|---|
| (a) Local time-series foundation models (Chronos, TimesFM) vs naive/AR/ridge | No | Low for returns: monthly industry returns have signal-to-noise near zero and E1's own in-sample t is 1.86. Possible value for volatility or volume forecasting, where persistence is real. Risk: both models were trained on public series that very likely include these exact files, so any "out-of-sample" window before the model's training cut-off is contaminated. | Preregister: target = next-month realised variance of the 49 industries (not returns); baselines = random walk, HAR/AR(1), ridge on lagged variance; metric = QLIKE with a Diebold-Mariano test (`overfitting.py:153`); test window = months after the model's stated training cut-off only. | 3-4 plan-days; CPU inference on 49 short series is minutes; weights are a download (needs owner approval under the no-download rule), Apache-2.0. | Later, and only for volatility. Not worth it for returns: the honest prior is a null, and the contamination caveat would dominate the write-up. |
| (b) Conformal prediction intervals | No | Real for the pre-trade cost band and for the change-point confidence (binary: use Venn-Abers or keep isotonic). For signal returns the interval would be honest and uselessly wide. | Split-conformal on cost residuals by time order; report empirical coverage of the 80/90 % band on later sessions against the current sigma band. Finite-sample guarantee needs exchangeability, so use a rolling calibration window and report coverage by month. | 2 plan-days; no dependency (20 lines of numpy). | Adopt later for the cost model (reviewer X's file); not for signals. |
| (c) SHAP-style explanations | No | None. The ridge is linear with three standardised features and no intercept: the exact attribution of a score is `coef_k * z_k`, already available from `CombinationResult.coef` (`ml_signals.py:327`). The detector is a threshold on robust z per feature; its "explanation" is the alarming feature, already in `Alarm`. SHAP would reproduce these numbers with a dependency. | n/a | 0 | Not worth it. Print `coef * z` per name in the alpha tool output instead (half a day). |
| (d) Drift monitoring for the ML models | Half: PSI reference stored per feature, `check_drift` implemented (`ml/registry.py:103-126,248`), never called; `drift.py` watches cassettes, RAG and pins, not models. PSI threshold 0.25 is the folk convention, not derived. | Medium: it is the cheapest missing control and a model-risk reader looks for it first. | Wire `check_drift` into the detector load path and the paper-trading daily job; set the threshold from the PSI null distribution at the actual sample size (simulate from the reference: with n = 60 minutes and 10 bins the null PSI is already about 0.15-0.3, so 0.25 would alarm on nothing-changed data); add a generator-hash check (Q-10). Report false-alarm rate on clean sessions. | 2 plan-days. | Adopt now. |
| (e) Purged k-fold / CPCV and PBO | Purged walk-forward yes (`ml_signals.py:113`); PBO written, unused (`overfitting.py:41`); CPCV no. | PBO and effective N on the 24-configuration trial matrix: high, it closes Q-03 and makes the DSR inputs defensible. CPCV for a 3-feature ridge whose six penalties give CV Sharpes of 0.0690 to 0.0699 (`model_cards.md:62`): none. | Build the 24-column daily return matrix per dataset, commit PBO with a bootstrap interval, `n_eff` by eigenvalues, and DSR at N = 24 against N = n_eff. Baseline: today's DSR. | 2-3 plan-days. | Adopt now for PBO and effective N; CPCV not worth it. |
| (f) Better covariance and risk-model validation | Ledoit-Wolf correct and unused; no ex-ante volatility, VaR or factor model anywhere in the risk report. | Medium-high: the risk report cannot currently state a predicted volatility, so nothing can be validated. | Add predicted book volatility from LW (and from a 1-factor plus specific model); validate by the bias statistic (std of realised return over predicted vol, target 1, with its sqrt(2/T) band) and by minimum-variance portfolio realised vol, sample vs LW, out of sample, on the synthetic 500 names and on the 49 industries (real, free, already on disk). | 4-5 plan-days. | Adopt now (the bias test on the 49 industries is a real-data result the project can actually pass). |
| (g) Missing and worth it | | | | | |
| g1. Factor-neutral gate and FF regression in E1-style reports | `residualise`, `factor_regression` unused; E1 has its own alpha regression | Closes Q-06 | False-PROMOTE rate of `market_corr` family before and after | 2 days | Adopt now |
| g2. Power curve at the live hurdle with bands | No | Closes Q-05 | n/a | 1 day plus about 2 h compute | Adopt now |
| g3. Preregistered live-window test for E1 (2026-10 on) and for the paper book | Live window is "descriptive only" | The only truly unseen data the project has; costs nothing | Fixed review date and minimum months stated now | 0.5 day | Adopt now |
| g4. Sharpe inference with skew/kurtosis and autocorrelation (Mertens; Lo's eta(q)) and minimum track record length reported next to every Sharpe | Only iid Lo, in three copies | Small but expected by this audience | Compare interval width on the E1 series against the stationary bootstrap already there | 1 day | Adopt with the Q-01 fix |
| g5. White reality check / Hansen SPA on the 24-configuration search | No | Would be the textbook answer to "24 trials"; overlaps with DSR and PBO | Null sweep FPR under SPA vs under t >= 3 | 3 days | Later; PBO plus effective N first |
