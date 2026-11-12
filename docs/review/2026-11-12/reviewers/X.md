# Reviewer X: execution, algos, TCA, paper trading, pre-trade risk, kill switch, ledger

Review of CEAP at tag v0.9.0 (worktree `review-2026-11-12`), 2026-11-12. Read-only; the only file
written is this one. Commands were run with the main checkout's venv and `PYTHONPATH=src` on this
worktree. Temporary files (a copy of `paper.db` from `origin/forward-log`, two scratch SQLite files)
were created in the session scratchpad and deleted.

## 1. Scope covered, and what I did not get to

Read in full: `src/ceap/execution/` (lob, algo, child, schedule, auction, impact, markouts,
cost_loop; rules.py first 80 lines), `src/ceap/analytics/` (implementation_shortfall, slippage,
market_impact, vwap, twap, execution_metrics), `src/ceap/ml/pretrade_cost.py` +
`pretrade_model.json` + `scripts/train_pretrade.py` + the model card, `src/ceap/paper/` (ledger,
fills, job, pretrade, lob_exec, feed, feeds_real, forward, cli), `src/ceap/persistence/`
(chainkey, anchor, killswitch, the chain parts of db.py), the MCP staging path
(`mcp/execution/staging.py`, the `stage_orders` tool in `server.py`), both paper workflows,
`scripts/check_forward_log_staleness.py`, `config/paper.json`, `docs/execution/execution.md`
(sections 1-7), `docs/quant/real-data-and-paper.md`, and the `origin/forward-log` branch
(CSV, REPORT.txt, reports, and the SQLite database itself).

Ran: Perold decomposition on known inputs (buy, sell, partial, overfill, pro-rated); POV band;
LOB self-trade and cancel; 40 TWAP runs and 20 large-parent runs in the simulator; auction
uncross known answers; NYSE calendar spot checks (2026-2028 holidays, half days, DST);
micro-dollar rounding; a schema-rollback attack on the keyed chain (two variants).

NOT covered, or only skimmed: `analytics/tca_aggregate.py` (551 lines, not read),
`analytics/real_costs.py`, `analytics/liquidity.py`, `paper/dividends.py`, `paper/config.py`,
`data/tca_universe.py` (only the impact and parent-generation references), the LULD state machine
in `rules.py` (lines 80-184), `harness/paper_gate.py` beyond its docstring and call sites,
`policy/engine.py` beyond its rule list, and the 113 tests for this area (I checked what exists
and what is missing by grep, I did not read test bodies or run test files). The regression
attribution (F8) was not reviewed. I could not check GitHub settings (branch protection on
`forward-log`, whether `PAPER_DAILY_ENABLED` is currently true, workflow run history).

## 2. Score: 5.5 / 10 against a top-firm bar

- The mathematics I checked is right: Perold legs sum exactly, sell signs are correct,
  Almgren-Chriss matches the paper, largest-remainder allocation is exact, the auction uncross
  follows the stated three rules, the calendar is correct on every date I tried.
- The writing is unusually honest about limits (synthetic everywhere, "no difference between the
  schedulers is detectable", the propagator fit failing on the quoted mid).
- But the flagship operational artefact, the forward log, contains a row recorded before its
  session opened, on a feed whose future is already generated, and has missed a session since.
- The headline tamper-evidence control (HMAC chain) can be defeated without the key by rolling the
  schema version back; the public log uses the published dev key and has no anchor.
- `ceap.execution` is a tested library that nothing in the product path uses by default; several
  controls described as running (replay gate, trading rules, cost loop) are not wired in.
- The pre-trade model is fitted to a generator that plants the same functional form; its card
  reports coverage only, and the coverage is systematically below nominal.

## 3. What is good (verified)

- Perold decomposition: `delay + spread + timing + opportunity + fees` equals the legacy total
  plus fees to 1e-12 for buys and sells, partial fills included
  (`src/ceap/analytics/implementation_shortfall.py:137`; ran BUY: 50 + 4.5 + 7.5 + 20 + 0.24 =
  82.24 bps against legacy 82.0 + 0.24; SELL mirrors with fees still positive).
- A partial fill with no final price is flagged (`opportunity_priced=False`) rather than silently
  reported as complete.
- `allocate` (`src/ceap/execution/schedule.py:41`) sums exactly to the parent; Almgren-Chriss
  `kappa`, `eta_tilde`, `E` and `V` match the 2000 paper's equations.
- LOB matching is correct price-time priority; iceberg refills lose priority; a resting order
  cannot cross (`src/ceap/execution/lob.py:154`). Fill mids are taken just before the fill
  (`src/ceap/execution/algo.py:77`), so the bot's own print does not contaminate its benchmark.
- Auction uncross: worked example reproduces (10.01, 800 matched, 0 imbalance); market-only and
  no-cross cases behave sensibly (`src/ceap/execution/auction.py:74`).
- Calendar: 2026-07-03 and 2027-12-24 are holidays (observed), 2026-11-27 and 2026-12-24 close at
  13:00, 2026-11-11 is a session, DST offsets are right (`src/ceap/data/calendar.py`).
- Paper day is one transaction keyed by `(book, date)`; a re-run is a no-op; the approval is
  reused by content hash after a crash (`src/ceap/paper/job.py:126`, `:422`).
- The t+1 convention is real: targets use rows `0..i`, orders staged at close `i` fill at close
  `i+1`, ADV is lagged one day (`src/ceap/paper/job.py:164`, `src/ceap/data/historical.py:154`).
- The 0.9.0 int-quantity hash fix is in place and correct: quantity is cast to float before
  hashing so it matches the REAL column read back (`src/ceap/paper/ledger.py:194`).
- The forward database on the branch has 20 fills, all whole shares, participation at most 0.8 %
  of ADV, costs 0.8 to 19.6 bps: internally consistent with the stated cost model.
- Docs do not call the simulator realistic; every execution claim carries an evidence class.

## 4. Findings

### X-01 [P0] The forward log holds a row recorded about 11 hours before its session opened; nothing stops future-dated rows
- Evidence: `origin/forward-log:paper.db`, `paper_days`: date `2026-11-10`, `recorded_at`
  `2026-11-10T03:42:15Z` (22:42 New York time on 9 November; the 10 November session opened at
  14:30 UTC). The branch's only commit is stamped 2026-11-10 03:42:18 UTC.
  `src/ceap/paper/job.py:129` checks only `day < forward_start`; there is no upper bound against
  the clock. `src/ceap/paper/feed.py:178` re-dates a pre-generated scenario, so every future row
  already exists.
- Status: VERIFIED (queried the database from the branch; read `run_day` and the CLI).
- Confidence: high
- Why it matters at a top firm: a forward record is evidence only because each row is written
  after the fact. A reader who compares `date` with `recorded_at` sees in one query that this one
  was not. The docs say the record "is never backfilled"; it is also not protected from being
  front-filled. The damage is limited because the feed is synthetic and labelled so, but the same
  code path will serve the real feed.
- Fix: in `run_day`, refuse any session whose close (`calendar.session_close(d)`) is later than
  now, for every feed; record the feed's as-of time in the row. Acceptance test: with an injected
  clock at 22:42 ET on day D-1, `run_day(D)` raises and writes nothing; the existing 2026-11-10
  row is disclosed in the docs as recorded early (do not rewrite it).

### X-02 [P1] The HMAC chain can be rewritten without the key by rolling the schema version back
- Evidence: `src/ceap/persistence/db.py:333` (`return 1 << 62, ""` when the schema is below v4)
  and `src/ceap/persistence/db.py:322` (the migration seals whatever legacy count it finds, with
  the real key). `src/ceap/persistence/chainkey.py:23` claims "a rewritten chain cannot pass
  itself off as legacy rows".
- Status: VERIFIED (ran it). With a secret key set, I wrote three audit rows; then, with raw
  SQLite access and no key, dropped the triggers, dropped the v4/v5 tables and `hash_alg` columns,
  changed one payload, recomputed plain SHA-256 links, restored the v3 triggers and set
  `user_version=3`. The next ordinary `Database(path, hmac_key=...)` open migrated, sealed
  `legacy_rows = 3` with the real key, and `verify_chain()` returned ok; it stayed ok after a new
  keyed row. Opening with `migrate=False` also verifies ok immediately.
- Confidence: high
- Why it matters at a top firm: the control is documented as stopping "someone who can write the
  database file but does not hold the key". It does not. Only an external anchor catches this,
  and none is committed (X-03).
- Fix: do not derive trust from data in the file. Store the keyed epoch outside the database, or
  refuse to treat any row as legacy unless a sealed `chain_epoch` row already exists, and make
  migration 0004 refuse to run on a non-empty chain without an explicit operator flag that is
  itself audited. Acceptance test: the attack above ends with `verify_chain().ok is False`.

### X-03 [P1] The public forward log has no working tamper evidence: published dev key, no anchor, no replay gate in the workflow
- Evidence: `origin/forward-log:paper.db` rows carry `hash_alg = hmac-sha256:efc0acdbba67`, which
  is `key_id(DEV_DEFAULT_KEY)` (`src/ceap/persistence/chainkey.py:40`, `:67`).
  `.github/workflows/paper-daily.yml:31` sets `CEAP_ENV: ci` and no key; the workflow
  (`:70` onwards) never runs `ceap db anchor`. No `audit_head.json` exists on either branch.
  `docs/threat-model/threat-model.md:56` says dev keys apply only when `CEAP_ENV=dev`.
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: anyone with repository write access can rebuild the ledger, the
  day chain and the plain SHA-256 CSV chain and force-push. The docs admit the dev key protects
  nothing and tell the owner to anchor and to protect the branch, but the one place the control
  matters does neither. The integrity of the record currently rests on GitHub alone.
- Fix: add an anchor step to `paper-daily.yml` that commits `audit_head.json` to `main` (a
  different branch from the data) and verifies the previous anchor before appending; set the key
  as a repository secret; correct T14. Acceptance test: a workflow dry run on a database whose
  earlier row was altered fails at the anchor check.

### X-04 [P1] The "runs daily" forward log has one run, two rows, and has already missed a session
- Evidence: `origin/forward-log` (fetched 2026-11-12 11:58 UTC) has a single root commit and rows
  for 2026-11-09 and 2026-11-10 only. 2026-11-11 is an NYSE session (`is_trading_day` returns
  True). `README.md:175` says "runs daily on synthetic data from 2026-11-09".
  `.github/workflows/paper-staleness.yml:10` and `:47`: weekly check, `--max-missed 2`.
- Status: VERIFIED for the missing row; SUSPECTED for the cause (I cannot see workflow runs).
- Confidence: high / low
- Why it matters at a top firm: the log is offered as proof that operations and controls run every
  day. Three sessions in, the evidence is one manual-looking run. The dead-man's switch would not
  fire for up to about seven sessions (weekly cadence plus two allowed misses).
- Fix: find out why the 11 November cron did not append; run the staleness check daily with
  `--max-missed 1`; state the number of runs honestly in the README until there are weeks of them.
  Acceptance: the branch shows one commit per session, each stamped after 21:30 UTC of its date.

### X-05 [P1] `ceap.execution` is not on any default product path, and three controls described as active are not wired
- Evidence: no module outside `src/ceap/execution/` imports `cost_loop`, `markouts`, `schedule`,
  `algo` or `rules` (grep). `src/ceap/execution/rules.py:1` says "US equity trading rules the
  simulator and child orders respect"; `lob.py`, `child.py` and `algo.py` never import it.
  `src/ceap/harness/paper_gate.py:5` says "Any difference blocks publication"; the module has no
  CLI and `paper-daily.yml` does not call it. `fills.py` uses the auction and LOB only when
  `moc_auction` or `engine="lob"` is set; `config/paper.json` sets neither.
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: the 0.8.0/0.9.0 notes list F1-F8, F12 and G11 as delivered. They
  are delivered as libraries with tests. The paper book still fills at the close with a formula,
  no predicted-versus-realised observation is ever recorded, LULD and tick rules constrain
  nothing, and the replay gate does not gate. A senior reader will ask "what uses this?".
- Fix: either wire them (record a `CostObservation` per paper fill; call the gate in the workflow
  before the push; apply `round_to_tick` and `LULDMonitor` in the simulator) or say plainly in
  `docs/execution/execution.md` and the CHANGELOG which pieces are library-only. Acceptance: a
  test that a paper day writes cost observations, and a workflow step that fails on a gate diff.

### X-06 [P1] The pre-trade model card reports no point error and no challenger, and its coverage is below nominal in 9 of 10 cells
- Evidence: `src/ceap/ml/pretrade_model.json` coverage: shifted 0.899, 0.906, 0.887, 0.859,
  0.883; in-family fresh seed 0.893, 0.895, 0.893, 0.893, 0.876. `scripts/train_pretrade.py:152`
  accepts anything in [0.85, 0.95]. The card (`scripts/train_pretrade.py:127`) prints coverage
  only. `src/ceap/data/tca_universe.py:286` plants impact as `gamma * sigma * sqrt(Q/ADV)`, the
  same form the model fits.
- Status: VERIFIED (numbers read from the committed file); the significance statement is my
  arithmetic: with about 1,600 parents per family the binomial standard error is about 0.75
  points, so 85.9 % is roughly 5 standard errors under 90 % and the pooled in-family mean (about
  89.0 % on about 8,000) is roughly 3 under, before allowing for parents that share a price path.
- Confidence: high on the facts, medium on the size of the shortfall
- Why it matters at a top firm: split-conformal guarantees marginal coverage under
  exchangeability. Missing it in-family on fresh seeds means the calibration set is not
  exchangeable with the test set (seed-level or session-level dependence), which is exactly what
  a validator looks for. The card gives no MAE, RMSE, bias or R-squared, no comparison with "half
  spread plus 0.6 sigma sqrt(Q/ADV)", and no coverage by size or algo bucket. The fitted `b` of
  0.379 against a planted 0.7 is explained in a sentence, not checked. The conformal quantile
  (1.653) is the normal 1.645 to two decimals, so conformal adds nothing here beyond a label.
  Recovering a square-root law from a generator that plants one is not evidence for the law.
  Note for the owner's queue: conformal intervals already exist (item (a) below), so the queued
  item is "make them honest", not "add them".
- Fix: add to the card: MAE, RMSE and mean signed error per family against the naive challenger;
  coverage with a session-clustered confidence interval; coverage by Q/ADV tercile and by algo;
  tighten the gate to an interval that 90 % must fall inside; calibrate by session (leave whole
  sessions out). State that the form is planted. Acceptance: the card shows these tables and
  `--check` runs in CI (it runs in no workflow today).

### X-07 [P2] POV's participation band does nothing
- Evidence: `src/ceap/execution/schedule.py:150`:
  `target = min(max(self.rate * market_volume, lo), hi, cap)` with `lo = (rate - band) V` and
  `hi = (rate + band) V`.
- Status: VERIFIED (ran `POV(rate=0.1, band=b).adjust(3, 0, executed, 10000)` for b in 0, 0.02,
  0.09 and executed in 0, 5000: always 1000).
- Confidence: high
- Why it matters at a top firm: `rate * V` always lies strictly inside its own band, so the clip
  is an identity and `executed` is ignored. A POV band exists to decide when to stop or catch up
  relative to what has been done. The docs repeat the formula as if it were a control.
- Fix: return `executed` unchanged when `executed` is inside `[lo, hi]`, else move to the nearer
  bound. Acceptance test: with V = 10,000, rate 0.10, band 0.02, executed 900 gives 900, executed
  700 gives 800, executed 1,500 gives 1,200 (no new shares).

### X-08 [P2] Schedulers do not complete the parent, and the last slice trades at its start
- Evidence: `src/ceap/execution/algo.py:106`, `:113` and `src/ceap/execution/child.py:90`: the
  final decision is taken at the start of slice N-1 and sends one IOC limited to the stale far
  touch.
- Status: VERIFIED (ran 40 seeds, 3,000 shares over 10 minutes, TWAP: fill rate min 0.916, mean
  0.997; the last fill is at 0.9000 of the horizon in every run. 60,000 shares: mean fill rate
  0.34, with a reported IS of -31 bps on one seed driven by a -24.6 bps opportunity leg).
- Confidence: high
- Why it matters at a top firm: "slices sum exactly to the parent" is true of the plan, not of the
  execution. There is no residual sweep, the last tenth of the horizon is idle, and an unfinished
  parent can show a flattering IS. The docs table reports fill rate 1.000 on eight seeds.
- Fix: decide at slice ends (or add a final decision at `end_ms - latency`), and let the final
  child walk the book up to a stated limit. Report fill rate next to every IS. Acceptance: over
  100 seeds the 3,000-share parent always completes and the last fill is after 0.95 of the horizon.

### X-09 [P2] LOB: no self-trade prevention, the bot can cancel anyone's order, and runs are not paired
- Evidence: `src/ceap/execution/lob.py:169` (match takes the queue head whatever its owner),
  `:334` (`self.book.cancel(payload[0])` with no owner check), `:341` (the diffusion draws from
  the same generator as the background flow, so the bot's own actions change the random stream).
- Status: VERIFIED (ran: an agent buy filled the agent's own resting sell, both sides were
  recorded as agent fills; the agent cancelled a background order by id).
- Confidence: high
- Why it matters at a top firm: a wash trade inflates executed quantity in `execute` (it sums all
  agent fills) and would be a compliance event on a venue. Unpaired paths mean two schedulers on
  "the same seed" see different markets, which is why eight seeds show nothing (SE about 5 bps).
- Fix: reject or decrement on self-match; check ownership in the cancel path; drive the efficient
  price and background arrivals from separate generators so strategies can be compared on common
  random numbers. Acceptance: a self-cross test, a foreign-cancel test, and a test that two
  different schedules see the same efficient-price path.

### X-10 [P2] Kill switch fails open in three places, and the MCP staging path ignores the paper risk config
- Evidence: `src/ceap/persistence/killswitch.py:41` (no table: state is inactive);
  `src/ceap/mcp/execution/staging.py:160` (no database: no halt); `src/ceap/mcp/execution/server.py:214`
  checks only the database switch, not `CEAP_PAPER_HALT`; `src/ceap/mcp/registry.py:46` builds the
  server without `limits`, so `server.py:52` uses default `RiskLimits()` and the `restricted` and
  `hard_to_borrow` lists in `config/paper.json` never reach `stage_orders`; `server.py:218` passes
  tier `""`, so `htb_tiers` can never match.
- Status: VERIFIED by reading; not exercised.
- Confidence: high
- Why it matters at a top firm: a kill switch that reads "not set" when it cannot be read is the
  wrong default, and a restricted list that applies on one order path but not the other is a
  classic control gap. Impact today is small: MCP-staged sets are never filled by the paper job.
  The check-then-act window in the job (`src/ceap/paper/job.py:213` read, `:422` commit) is
  seconds and acceptable at daily frequency; say so in the docs. `stage_orders` also accepts an
  unbounded `gross_notional` when called directly (the 1e10 bound lives in `platform.py`).
- Fix: fail closed when the switch cannot be read; have `stage_orders` call `halt_status(db)`;
  load `RiskLimits` from the same config in the registry; bound `gross_notional` in the tool.
  Acceptance: a restricted name in `config/paper.json` is blocked by both paths; staging with no
  database and `CEAP_PAPER_HALT` set is refused.

### X-11 [P2] Missing pre-trade limits a desk would expect
- Evidence: `src/ceap/paper/pretrade.py:171` (`check_orders`): restricted, hard to borrow, price
  band, optional sigma band, ADV percentage, per-name weight, gross. Nothing else.
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: there is no absolute per-order notional or share limit, no order
  count or daily turnover limit, no net-exposure limit, and no loss or drawdown trigger that sets
  the kill switch. The "fat-finger" band tests the day's close against yesterday's close; it says
  the print is odd, not that the order is. Clips at `:203` and `:210` produce fractional shares
  even when `fractional_shares` is false.
- Fix: add max order notional, max orders per day, max net, and a drawdown rule that sets the
  switch with an audit row; floor clipped quantities to whole shares. Acceptance: one test per
  limit, including a breach that leaves a `risk_block` event.

### X-12 [P2] Books of record are binary floats; integer micro-dollars exist only inside hashed bodies
- Evidence: `src/ceap/paper/fills.py:137` (`cash: float`); `src/ceap/paper/job.py:281` and
  `src/ceap/paper/ledger.py:332` reconcile at a relative `1e-6`, which is 10 dollars on the
  10-million-dollar book.
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: F10 was met as worded (money as integers in ledger bodies), and
  the all-integer cash check is a real improvement. But state, equity and share counts are still
  floats, the default tolerance is loose, and the reconciliation compares the events with a state
  computed by the same code in the same call, so it catches arithmetic slips, not an independent
  break.
- Fix: carry cash as integer micro-dollars (or `Decimal`) in `BookState`; reconcile exactly;
  keep the float only for reporting. Acceptance: a 5,000-day property test with zero cash drift.

### X-13 [P2] Markout confidence intervals treat fills as independent
- Evidence: `src/ceap/execution/markouts.py:52` (bootstrap over individual fills).
- Status: VERIFIED by reading; JUDGEMENT on the size of the effect.
- Confidence: medium
- Why it matters at a top firm: fills of one parent in one minute share the same later mid. An
  i.i.d. bootstrap gives intervals that are too narrow, and the routing table then labels a venue
  "adverse" on that basis, across several venues with no multiplicity control.
- Fix: resample parents or sessions (cluster bootstrap); note the number of clusters next to each
  interval. Acceptance: on a synthetic set with zero true venue effect, the "adverse" rate is at
  or below the nominal level.

### X-14 [P2] Interval VWAP and participation include the order's own prints
- Evidence: `src/ceap/data/synthetic.py:534` (`trades.extend(own)`),
  `src/ceap/analytics/execution_metrics.py:42` and `:276`.
- Status: VERIFIED by reading.
- Confidence: high
- Why it matters at a top firm: including own volume is the usual definition of interval VWAP, but
  at 10 to 25 % participation it pulls the benchmark towards the fill price by that fraction. Desks
  report both. Only one is offered here, and the simulator's POV measures against volume
  excluding the bot, so the two halves of the repo use different definitions of participation.
- Fix: add ex-own VWAP and ex-own participation to `ExecutionMetrics` and name both. Acceptance:
  a known-answer test where own volume is half the tape.

### X-15 [P2] Real feed: Stooq history is described as split-adjusted only; if it is also dividend-adjusted, the dividend option double counts and replays will not match forward runs
- Evidence: `src/ceap/paper/feeds_real.py:9`; CHANGELOG 0.9.0 "Yahoo dividends are
  split-adjusted, which matches the split-adjusted Stooq closes".
- Status: SUSPECTED (I believe Stooq's default history is adjusted for dividends as well; I could
  not check without the files or a network call).
- Confidence: medium
- Why it matters at a top firm: on a dividend-adjusted series, past closes are restated at every
  ex-date. A forward run marks from stored marks (price return, plus the cash dividend if F13 is
  on), while a backfill or the replay gate over the restated file gets total return and then adds
  the dividend again. The feed is not enabled, so nothing is wrong today. Also `:92` uses
  `date.today()` on the machine's clock, not New York.
- Fix: before enabling, compare one stock's Stooq close with an unadjusted source across an
  ex-date and record the answer in the docs; hash each day's input file into the day row.

### X-16 [P2] Cost-loop recalibration is adopted automatically with no guard, and three impact coefficients coexist
- Evidence: `src/ceap/execution/cost_loop.py:143` (adopts any finite fit from three or more
  observations); coefficients 0.6 (`cost_loop.py:36`, `src/ceap/analytics/market_impact.py:46`),
  0.379 (`src/ceap/ml/pretrade_model.json:23`), planted 0.7 (model card).
- Status: VERIFIED
- Confidence: high
- Why it matters at a top firm: a weekly job that overwrites a pricing parameter from a handful of
  noisy orders, with no bounds, no minimum sample and no approval, is a model-change control gap.
  The TCA's "expected impact" and the paper cost model use different constants for the same law.
- Fix: require a minimum sample, bound the step, write the change to the audit log, and name one
  source of truth for the coefficient.

### X-17 [P3] Smaller items
- `src/ceap/analytics/twap.py:29`: the last observation gets the median gap as its weight.
  Verified: prices 100 then 200 ten seconds apart give 150; a price that prevailed for the whole
  window should give 100. Take the window end as an argument.
- `src/ceap/analytics/implementation_shortfall.py:170`: with `opportunity_quantity` set, the delay
  leg is charged on that quantity, while the docstring says the whole target. Verified (25 bps
  instead of 50). State it or change it.
- `src/ceap/analytics/execution_metrics.py:38`: 23,400 seconds is used on half days, and daily
  close-to-close sigma is scaled to an intraday horizon (overnight variance included).
- `src/ceap/data/session.py:26`: `previous_sessions` ignores holidays, so a matched baseline can
  land on a closed day.
- `src/ceap/paper/fills.py:362`: the short-sale restriction applies only on the trigger day
  (Rule 201 also covers the next day) and is modelled as a reject, not a price test.
- `src/ceap/paper/fills.py:318`: every exit from the universe is cashed at the last mark; a
  bankruptcy would be flattered. Stated in the event, worth a line in the docs.
- `src/ceap/execution/child.py:78`: a cancelled child is counted back into the gap before the
  cancel is acknowledged; a fill in the latency window can overfill the parent. SUSPECTED (zero
  overfills in my 60 runs).
- `src/ceap/execution/lob.py:381`: passive fills add signed impact in the bot's direction, which
  softens adverse selection on resting orders. JUDGEMENT.
- `src/ceap/paper/cli.py:65`: the report prints an in-sample Sharpe of 0.63 computed from the
  synthetic backtest, while `config/paper.json` and the docs say it is null. The docs' "Forward
  result: UNKNOWN" is also inexact for a seeded feed: it is knowable today, just not informative.
- `scripts/train_pretrade.py:107`: the card's scale formula omits the `(1 + kappa)` factor.
- `src/ceap/paper/lob_exec.py:68`: re-basing to the mid before each slice removes the order's own
  impact, so the "measured" cost is about half a tick whatever the size. Disclosed; off by default.

## 5. Plan of 2026-11-09: status of the items in my area

| Item | Status | Comment |
|---|---|---|
| F1 LOB simulator | done, weak | Correct matching and queue model; no self-trade prevention, unpaired randomness, uncalibrated (X-09). Honestly labelled. |
| F2 endogenous impact | done, weak | Recovers the kernel on the latent efficient price; fails on the quoted mid (7.49 against 2.0), which the docs report. Linear in size. |
| F3 schedulers | done, weak | AC and allocation correct; POV band is inert (X-07). |
| F4 child logic | done, weak | No completion logic; cancel/replace loses the queue every time (docs admit the second) (X-08). |
| F5 auction + paper MOC cap | done | Uncross correct; impact on a seeded synthetic book; off by default. |
| F6 cost loop | done, not wired | No caller; auto-adoption unguarded (X-05, X-16). |
| F7 markouts and routing | done, weak | Not used by the TCA or agents; i.i.d. bootstrap (X-13). |
| F10 money as integers | done as worded, weak | Only in hashed bodies; state is float (X-12). |
| F11 staging, kill switch | done, weak | Fail-open paths and split risk config (X-10). |
| F12 tick, lot, LULD, sigma band, halts | done, not wired | The simulator applies none of it (X-05). |
| F13 dividends | done, risk | Possible double count on the real feed (X-15). |
| F14 clean-ups | done | Deprecations warn; `vwap_by_bucket` uses bincount. |
| A6 keyed chain and anchor | done, not convincing | Rollback attack works; no anchor in use (X-02, X-03). |
| G6 dead-man's switch | done, weak | Weekly, two misses allowed; a session is already missing (X-04). Branch protection left to the owner; not verifiable here. |
| G11 replay gate | done, not wired | Not in the publishing workflow (X-05). |
| D22 naive challenger on each card | not done for the pre-trade card | X-06. |

## 6. Candidates: present now / value / how to measure / cost / verdict

All local and free.

**(a) Conformal intervals for pre-trade cost.**
Present now: yes, split-conformal on normalised residuals (`src/ceap/ml/pretrade_cost.py:150`,
`:240`), reported in the TCA as `pretrade_interval_bps`. Value here: low as built (q equals the
normal quantile; coverage under nominal). The useful step is to fix the calibration: calibrate
by held-out session, add Mondrian (per algo and per size tercile) quantiles, and report
conditional coverage. Measure: coverage with session-clustered intervals against today's
0.859 to 0.906, and interval width at equal coverage. Cost: 1.5 plan-days, seconds of compute.
Verdict: adopt now (as a repair). It is already advertised.

**(b) Bandits for algo selection in the simulator.**
Present now: no. Value: none yet. The repo's own table shows four schedulers within one standard
error on eight seeds; a bandit would learn noise. It also needs context (spread, volatility,
size) that the zero-intelligence book barely varies. Measure, if ever: regret against the best
fixed arm on common random numbers over at least a few thousand parents. Cost: 3 plan-days plus
the pairing fix in X-09 first. Verdict: not worth it now; revisit after (e) and X-09.

**(c) Reinforcement learning for execution, simulator only.**
Present now: no. Value: negative for credibility unless done as a cautionary study. In this book
the efficient price is a driftless diffusion plus the bot's own linear propagator, so the
optimal policy is known in form (Obizhaeva-Wang style: block, steady rate, block) and an agent
can only rediscover it or exploit artefacts: the fixed 2 ms latency, stale quotes that are
cancelled rather than picked off, the bot's own passive fills pushing the price its way
(`lob.py:381`), a background flow that never reacts to the bot's footprint, and no other
informed trader. Most published wins over TWAP are of this kind: the learner fits the
simulator's response function, and the edge vanishes on replayed data where the market does not
respond or responds differently. A strict protocol would be: common random numbers, at least
10,000 held-out episodes, benchmark against the closed-form optimum and TWAP, report the
difference with a confidence interval, then re-test with perturbed simulator parameters. Cost:
8 to 12 plan-days, hours of CPU. Verdict: not worth it. If the owner wants the topic covered,
write the two-page "why this would be an artefact here" note instead (0.5 day).

**(d) SHAP-style explanations for the pre-trade model.**
Present now: not needed. The model is linear in three terms per algo; the coefficients are the
explanation, and Shapley values for a linear model are just coefficient times (feature minus
mean). Value: a per-order three-bar breakdown (intercept, spread term, impact term) in the TCA
output is useful and already available from `PretradeEstimate.impact_bps`. Cost: 0.5 day for the
breakdown; a SHAP dependency would add nothing. Verdict: not worth it as SHAP; adopt the plain
additive breakdown later.

**(e) A more credible simulator.**
Present now: a zero-intelligence book with an informed tilt, illustrative parameters
(`src/ceap/execution/lob.py:193`). Options:
1. Queue-reactive model (Huang, Lehalle and Rosenbaum, 2015): arrival and cancel intensities
   depend on queue sizes. Adds realistic queue dynamics; still needs level-two data to
   calibrate, which is not free at any useful scale.
2. Replay of historical data. Free sources I know of: LOBSTER's public sample files (one day,
   five large-cap names, 2012-06-21, up to ten levels) which are a manual download from a public
   page and need no account; the FI-2010 Helsinki benchmark (five stocks, ten days, normalised,
   poor for execution work); IEX historical DEEP and TOPS files (free, real, but one small venue
   and tens of gigabytes a day); crypto exchange depth feeds (free, wrong asset class). The owner
   has decided against LOBSTER and Nasdaq sign-ups, so only the no-account samples qualify, and
   the owner should confirm even those.
Value: one real day is enough to check three things the current book cannot: the spread and depth
distributions, the fill probability of a touch order by queue position, and the short-horizon
markout of passive fills. Replay has its own artefact (no market response to the bot), which must
be stated. Measure: calibrate the existing parameters to the sample day and report the three
distributions simulated against real. Cost: 5 to 7 plan-days, one manual download, minutes of
compute. Verdict: later, and only as a calibration check with its limits written down. Do X-09
first.

**(f) Missing and worth it for a senior execution reader.**
1. A future-date guard and an as-of stamp on every forward row (X-01). Half a day. Adopt now.
2. Anchor in the workflow and a fix for the rollback hole (X-02, X-03). 2 days. Adopt now.
3. Common random numbers plus a properly powered scheduler comparison: paired differences against
   TWAP over 500 or more seeds, with the Almgren-Chriss frontier (expected cost against standard
   deviation) plotted from simulation next to the closed form. This is the one study that would
   show the simulator and schedulers working together. 2 days. Adopt now, after X-07 to X-09.
4. Ex-own VWAP and participation (X-14), and a peer-relative z-score in the TCA. 1 day. Later.
5. Wire the cost loop to the paper book and publish the reliability table with the forward log
   (X-05). 2 days. Later.
6. A drawdown-triggered kill switch and absolute order limits (X-11). 1.5 days. Adopt now.
7. A queue-aware child rule (keep the order while the queue ahead is short), which the docs
   already name as the next step, measured by passive share and markout on paired seeds. 1.5 days.
   Later.
