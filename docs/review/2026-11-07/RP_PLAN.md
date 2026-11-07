# RP: paper trading (requested by the owner on 2026-11-07)

Branch `rp-paper`, based on `r4-platform` (`53a50fb`: SQLite persistence, persisted staged orders,
replay bundle, approval semantics). Ships in the 0.7.0 combined PR with R5. Everything here is
**paper, synthetic or free daily data, not trading**: no order ever leaves the process, no broker, no
paid data, no live model calls (the daily job uses no model at all).

Today approved proposals stage PAPER orders in `PAPER_RESEARCH` and nothing fills them. RP adds the
missing half: a simulated fill engine, a position and cash ledger, a daily job, and a forward record
kept apart from every backtest.

## Conventions (stated once, used everywhere)

* **Timing.** Same as the backtest (2.1): the target is built from closes up to decision date `D` and
  filled at the close of the next session `D+1`. Orders staged on `D` are filled by `run-day D+1`.
* **Costs.** Imported from `ceap.analytics.backtest` (`CostModel`, `FinancingModel`, `PositionCaps`,
  `risk_free_daily`, `rolling_vol_bps`, `SSR_TRIGGER`), not copied: half the quoted spread plus
  square-root impact `Y * sigma_d * sqrt(participation)`, tier borrow on short market value, rf on
  cash (short rebate) and rf plus the financing spread on negative cash. Fees: a per-share and a
  minimum-ticket fee, both 0 by default (stated) because the backtest charges none.
* **Units.** Dollars and shares. Initial capital = `gross_notional` of the config, so a ledger
  replay of backtest fills compares directly with the backtest's NAV times `gross_notional`.
* **rf.** `data/reference/rf.csv` when present; otherwise rf = 0 and the report says so.
* **Labels.** Every report, CSV header and CLI line carries
  `paper, synthetic or free daily data, not trading`.

## Items

| Id | Item | Files | Effort | Done when |
|---|---|---|---|---|
| RP.0 | This plan | `docs/review/2026-11-07/RP_PLAN.md` | 0.5 h | Committed first |
| RP.1 | Simulated fill engine: open orders fill at the next session's close; per-name participation cap (`PositionCaps.max_participation` x lagged ADV) with carry-forward of the unfilled quantity to the next session (an order works at the cap until filled or superseded by a newer staging; `order_ttl_days` > 0 adds an expiry, off by default); costs via the imported backtest cost model; rejects recorded with a reason: missing or halted price (quantity carries), not a member (cancelled), SSR (short-increasing part blocked when `low/prev_close - 1 <= -10 %`, the backtest's rule); splits applied on the ex-date to positions and open orders, cash unchanged | `src/ceap/paper/fills.py`, `src/ceap/paper/feed.py` | 6 h | Known-answer tests: cap on 3 names x 3 days, carry-forward fills the residual next day exactly; half-halted day; SSR blocks only the increase; 2-for-1 split leaves equity unchanged to 1e-9 and doubles shares; cost of every fill equals `CostModel.trade_cost_bps` x notional to 1e-12 for 20 random trades |
| RP.2 | Position and cash ledger: append-only SQLite tables in migration `0003_paper.sql` on R4's `Database` (UPDATE/DELETE blocked by triggers); events `order`, `fill`, `reject`, `cancel`, `split`, `financing`, `borrow`, `mark`; per-book hash chain on canonical bytes like the audit log; daily snapshot with equity, cash, long/short MV, gross/net exposure, turnover, costs; reconciliation: positions equal the sum of signed fills adjusted by splits, cash equals initial capital plus the sum of cash deltas | `src/ceap/paper/ledger.py`, `src/ceap/persistence/migrations/0003_paper.sql` | 6 h | Two-name, three-day ledger worked by hand matches to 1e-9; tampering one row breaks `verify_chain` at that row; reconciliation passes after 60 synthetic days and fails when a fill row is altered; UPDATE on a ledger row raises |
| RP.3 | Daily job `ceap paper run-day --date D`: idempotent per (book, date), one SQLite transaction per day (crash mid-day leaves nothing; re-run completes); recomputes the signal on the point-in-time panel (rows up to D only), builds the target (same `target_weights`, caps and membership as the backtest), stages the delta orders through the SAME `QueuedApprovalGateway` with `SqlApprovalStore` and the hash-chained audit log; principal `paper-auto` may decide only when `CEAP_ENV=dev` or with `--allow-paper-auto` (audited), else the job refuses. `ceap paper backfill --from --to` (simulated book only) and `ceap paper report`. Market data behind a `DailyFeed` protocol returning a `MarketPanel`; `SyntheticFeed` now, an adapter hook for R5's Stooq connector at integration | `src/ceap/paper/job.py`, `src/ceap/paper/feed.py`, `src/ceap/paper/config.py`, `src/ceap/paper/cli.py`, `src/ceap/cli.py` (a two-line registration only), `config/paper.json` | 7 h | Re-run of a day is a no-op (row counts and chain head unchanged); injected crash after fills then re-run equals an uninterrupted run byte for byte (chain head); future rows perturbed do not change the target (no look-ahead); non-dev without the flag refuses and writes nothing; approval requested and decided rows present in the audit chain |
| RP.4 | Forward record: `paper_forward_log` table kept apart from `paper_simulated_log` (backfill writes only there), both hash-chained; forward start date fixed in `config/paper.json`, a `run-day` before it is refused; CSV export with `schema_version` and the chain; report: forward Sharpe (excess of rf) with 95 % CI (Lo 2002 iid standard error), days elapsed, minimum days to distinguish from the in-sample Sharpe (two-sided alpha 0.05, power 0.8), comparison against in-sample, label on every line. `paper-daily.yml`: cron after the US close, runs `run-day`, appends the log to branch `forward-log`; the job's `if:` requires repo variable `PAPER_DAILY_ENABLED == 'true'`, so it is disabled until the owner sets it | `src/ceap/paper/forward.py` (stats, report, CSV), `.github/workflows/paper-daily.yml` | 5 h | Backfill rows never appear in the forward table; CSV round-trips and its chain verifies; Sharpe CI covers the true Sharpe in >= 93 % of 400 simulated iid series (nominal 95 %, tolerance 2 pp); min-days `T = (z_0.975 + z_0.8 sqrt(1 + s^2/2))^2 / s^2` equals 3 hand cases (S = 1, 0.5, 2: 1980, 7913, 496); workflow file parses and its job is guarded by the variable |
| RP.5 | Docs: COOKBOOK recipe, README section, ARCHITECTURE diagram update, LEARN paragraph (plain idea, formal, worked example), CHANGELOG `## Unreleased` | `docs/*.md`, `README.md`, `CHANGELOG.md` | 2 h | Banned-word check passes; every number in the worked example is reproduced by a test |
| RP.6 | Equivalence and invariants | `tests/unit/paper/*`, `tests/integration/test_paper_*` | 4 h | Replaying 60 days of the backtest's own fills through the ledger equals `simulate_backtest` NAV x capital within 1e-9 (financing on, rf supplied); hypothesis property (200 examples, derandomised): cash + position value = equity and equity - capital = cumulative mark P&L - cumulative costs - cumulative borrow + cumulative financing, to 1e-6 relative; ruff and mypy clean; second-reader review of the ledger maths |
| RP.7 | Pre-trade risk checks on every staged order: restricted list (no increase, no flip), per-name weight cap, gross cap (gross-increasing orders dropped), max order 25 % of ADV, fat-finger band at staging (decision close vs previous close) and at fill (fill vs decision price); a breach is removed, never traded, and written as a `risk_block` ledger event plus a `paper.risk_block` audit row | `src/ceap/paper/pretrade.py`, `src/ceap/paper/job.py` | 5 h | One unit test per rule with the allowed quantity worked by hand (6 rules); an all-hard-to-borrow book over 7 sessions never goes short and audits every block |
| RP.8 | Kill switch `CEAP_PAPER_HALT` (value = reason): no staging, every working order cancelled, `halt` event and audit row, reason in the daily artefact | `src/ceap/paper/pretrade.py`, `src/ceap/paper/job.py` | 1 h | Halted session leaves no open orders and stages nothing; the reason appears in the ledger, the audit log and the report |
| RP.9 | NYSE calendar from `data/calendar.py`: no run on a weekend or holiday (CLI exit 3), every booked feed row must be a session, half days flagged `early_close` | `src/ceap/paper/job.py`, `src/ceap/paper/cli.py` | 1 h | Thanksgiving 2026-11-26 refused; 2026-11-27 booked with `early_close = true` |
| RP.10 | Borrow availability: names (or liquidity tiers) on the hard-to-borrow list cannot increase a short, at staging and again at fill | `src/ceap/paper/pretrade.py`, `src/ceap/paper/fills.py` | 1 h | Short increase blocked to the floor `min(q, 0)` in both places, with the ids of the order |
| RP.11 | End-of-day reconciliation inside each session, before commit: positions and cash recomputed from every fill, split and delist against the book; target vs actual drift (target minus position minus working orders minus knowingly waived quantity) above 0.5 % of equity per name is a break; any break fails the run loudly (exception, CLI exit 4, nothing committed, break report written) | `src/ceap/paper/job.py` | 3 h | Injected position break and injected drift break each raise, commit nothing and write a `FAILED_RECONCILIATION` artefact; 60 clean sessions pass |
| RP.12 | Daily P&L attribution: signal (P&L of the targeted shares), drift (held minus targeted), financing, borrow, costs; sums to the equity change | `src/ceap/paper/fills.py` | 2 h | Hand case (1200 targeted vs 1000 held) gives signal 4600 and drift -600; sum equals the equity change to 1e-9 |
| RP.13 | Per-run report artefact `<report_dir>/<book>/<date>.json` and `.md` (snapshot, attribution, fills with ids, exceptions, reconciliation, halt) | `src/ceap/paper/job.py` | 2 h | Both files written for every session, including failed ones |
| RP.14 | Traceability: every order, fill, reject and cancel carries `order_id`, `staging_id` and `approval_id`; order ids are prefixed by the staging id | `src/ceap/paper/fills.py`, `src/ceap/paper/job.py` | 1.5 h | Every fill and order event over 8 sessions resolves to a `staged_orders` row and an APPROVED `approvals` row |
| RP.15 | Desk-control tests and docs | `tests/unit/paper/test_pretrade.py`, `tests/integration/test_paper_controls.py`, docs | 3 h | As above; ruff and mypy clean |

Total estimate: 30.5 h for RP.0 to RP.6, plus 19.5 h for the desk controls (RP.7 to RP.15) the owner
added on 2026-11-07: **50 h**, about 6 plan-days and 2.5 wall-clock days.

Working orders: a fill clipped by the participation cap keeps working at the cap until it is filled or
replaced by the next target (`order_ttl_days = 0`), the convention of the backtest on `rf-data`
(`289243b`). The paper engine works the residual share quantity; the backtest works toward the target
weight. They differ only in how far prices move while a residual is being worked (a few bps a day).
Daily frequency only: intraday fills would need paid data, a non-goal.

## Interfaces left for integration

* **R5 Stooq feed.** `ceap.paper.feed.DailyFeed` is a `Protocol` with one method, `panel() ->
  MarketPanel`, plus a `name`. R5's connector plugs in through `register_feed("stooq", factory)`
  from an integration shim; no R5 file is edited. Missing spreads on real data fall back to the R5.4
  schedule passed in by the adapter.
* **R2 capacity carry-forward fix.** The paper engine carries unfilled quantity itself; when R2's
  backtest change lands, the equivalence test still feeds explicit fills, so it is unaffected.
* **Migration number.** `0003_paper.sql` assumes no other lane adds `0003`; the coordinator
  renumbers at integration if one does (the loader rejects gaps, so a clash fails loudly).

## Non-goals

No broker, no order routing, no intraday fills, no live data subscription, no model in the daily job,
no enabling of the workflow or setting of repository variables by this round.

## Deviations recorded during the build

* `tests/unit/test_r4_persistence.py` asserted `LATEST_VERSION == 2`; it now asserts `>= 2` and
  compares with `LATEST_VERSION` (a two-line change; 0003 is new).
* The CLI lives in `src/ceap/paper/cli.py`, registered from `cli.py` the way R4 registers `ceap db`.
* The second reader's findings were applied: the day chain now covers the `equity` column,
  reconciliation also checks equity, the exact minimum-days formula is used, and stale marks are dropped.
