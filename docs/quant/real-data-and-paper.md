<!-- Not part of the RAG knowledge corpus (data/reference/knowledge/manifest.json lists quant.md only),
     so release notes here never shift retrieval calibration. -->
# Real data (R5) and paper trading (RP), 0.7.0 and 0.7.1

**R5, evidence class VOID.** Source: `docs/research/result_momentum_2026-11.md`. Point-in-time
coverage 75.1 % against a required 95 % (worst month 53.9 %); the Stooq cross-check was not run. No
test statistic was computed. Descriptive pipeline checks on the same deficient sample (not evidence
about momentum): correlation with Ken French UMD 0.869 over 228 months, FF5 + UMD R² 0.77, 0 of 613
series hard-failed the quality gate. The breakeven figure of -38 bps one way is disclosed there
because it partly reveals the sign of gross returns. The pseudo-holdout (2024-01-01 to 2025-12-31)
is unspent; R5 is rerun only under a new preregistration (D20).

**R5 v2 attempt (2026-11-08), evidence class VOID.** Source:
`docs/research/result_momentum_2026-11-v2.md`. With the owner's hand-downloaded Stooq bulk file as
primary vendor, measured in-sample coverage was 74.9 % (worst month 49.7 %) against 95 %: Stooq has
no delisted names and 337 of its 606 matching series start after 2004. The Stooq vs yfinance
cross-check passed (99.56 % of 2,620,341 name-days within 10 bps). The attempt stopped before a v2
preregistration; no statistic was computed and the pseudo-holdout is still unspent.

**RP, evidence class synthetic (no forward result yet: UNKNOWN).** The daily job was enabled on
2026-11-08 (repository variable `PAPER_DAILY_ENABLED=true`) and the first forward session is
2026-11-09. The paper book fills at the next
close at `|x| p (h + 0.6 sigma sqrt(|x|/ADV)) / 1e4`, capped at 10 % of ADV, with the backtest's own
cost, financing and position-cap code. The hand-checked two-name example in LEARN §18a (checked by
`test_two_name_three_day_ledger_by_hand`) ends day two at equity $1,002,445.07. The feed is
synthetic; the Stooq feed refuses rows up to 2025-12-31 and is not configured (no Stooq data is in
the repository), so the earliest
real-price start is about January 2027. Distinguishing a Sharpe of 1 from 0 needs about 1980
sessions (LEARN §18a), so the forward record tests operations and controls, not profitability.

**Research realism (0.8.0, WS-D), evidence class simulation.** Source: `docs/research/README.md`
("Research realism toolkit"). None of this is a real-data result. The synthetic templates are now
labelled realistic or stylised; the realistic momentum template (rank IC 0.025, gross Sharpe 0.42)
gets the new `INCONCLUSIVE` verdict, because 14 years of in-sample data cannot separate it from zero
or from a tradable edge. The IC hurdle is a priori (3.0 for a searched result, 1.645 for one
preregistered test); the re-run null sweep gave 0 false PROMOTEs in 500 seeds at 3.0. Data vintages
of every real download can now be hashed and diffed (`ceap.data.real.vintage`), and
`scripts/rebuild_study.py` checks a study's preregistration hash before anything is re-run. The next
real-data study (E1) runs under its own preregistration and is not part of this change.

**Accuracy bars for the research evaluation (0.8.0), evidence class simulation.** The old bars (0.85 on
clear scenarios and on all thirty, 0.5 on near-threshold ones) were set before the D1 recalibration
and the third verdict. They are replaced by a-priori bars derived from generator parameters, never
from a run. For each scenario, `ceap.data.ground_truth.hit_probability` gives the chance of reproducing
the expected verdict. It treats the gate statistics as independent normals around their closed-form
expectations, so `P(PROMOTE) = prod Phi(z)`, and the realised full-period net Sharpe as normal with
the Lo (2002) standard error, which gives the INCONCLUSIVE band. Flags defined by the scenario's
parameters (concentration, limits) count as certain. Each bar is the 5 % one-sided lower bound of the
Poisson-binomial hit count (`accuracy_bar`). With the 0.8.0 templates the expected accuracies and bars
are: clear 0.951 (bar 0.875, n = 24), near-threshold 0.665 (bar 1/3, n = 6), all 0.893 (bar 0.80,
n = 30). The deterministic pipeline measured 21/24, 2/6 and 25/30, each at or above its bar. The
clear result sits exactly on its bar, which is a margin of zero and worth watching. The independence
assumption is an approximation: the gates share data, so the true spread of the hit count is
probably wider.

**Paper controls added in 0.8.0, evidence class design (tested offline, no forward result yet).**
The paper ledger chain is HMAC-SHA256 under `CEAP_AUDIT_HMAC_KEY` (without it, a published
development key is used and reports say `dev_key: true`); the public forward and simulated log CSVs
stay plain SHA-256 so anyone can verify them. A DB-backed kill switch (`ceap db kill-switch
set|clear|status`) is checked by the paper job and by `stage_orders`, next to `CEAP_PAPER_HALT`.
`stage_orders` runs the pre-trade checks before staging and persists staged sets in SQLite. A failed
`paper-daily` run opens a `paper-failure` issue, and the weekly `paper-staleness` workflow opens a
`paper-stale` issue when the `forward-log` head is more than 2 NYSE sessions old. Source:
`CHANGELOG.md` (Unreleased) and COOKBOOK recipe 43.

## Paper trading and the forward log (moved from the README in 0.9.0)

paper, synthetic or free daily data, not trading. Approved targets are staged as PAPER orders,
filled by a simulator at the next session's close with the backtest's cost model, and booked in an
append-only, HMAC-chained SQLite ledger (`CEAP_AUDIT_HMAC_KEY`; anchor the heads with `ceap db anchor`). The forward record starts on the fixed date in
`config/paper.json` and is never backfilled.

```bash
ceap paper run-day --date 2026-11-13 --db var/paper/paper.db
ceap paper report --db var/paper/paper.db
ceap paper backfill --from 2026-06-01 --to 2026-10-30 --db var/paper/paper.db
```

The feed is synthetic today (scenario R01, re-dated so its out-of-sample rows are the forward
period). Desk controls run every session: pre-trade risk checks, a kill switch (`CEAP_PAPER_HALT`, or the
DB-backed `ceap db kill-switch set|clear|status`, checked by the paper job and by `stage_orders`),
borrow availability, the NYSE calendar, an end-of-day reconciliation that fails loudly, P&L
attribution and a markdown/JSON artefact per run. A free daily feed plugs in through `ceap.paper.feed.register_feed`. The scheduled workflow
`paper-daily.yml` runs when the repository variable `PAPER_DAILY_ENABLED` is `true`, which the owner
set on 2026-11-08; it appends to the `forward-log` branch. See COOKBOOK recipe 43 and
LEARN section 18a.

**Last recorded session:** see the head of the
[`forward-log` branch](https://github.com/AshJha0/AgenticAICashEquities/commits/forward-log) (the last
line of `forward_log.csv`). Two dead-man's switches watch it: a failed `paper-daily` run opens or
updates an issue labelled `paper-failure`, and the weekly `paper-staleness` workflow fails and opens
an issue labelled `paper-stale` when the branch head is more than 2 NYSE sessions old. The owner
should protect `forward-log` against force-push and deletion (Settings > Rules), since the
record's value is that it is append-only.

**What the forward log is, and what it is not.**

- It runs on **SYNTHETIC data, not real prices** (evidence class: synthetic). `config/paper.json`
  sets `"feed": "synthetic"` and `in_sample_sharpe` to null.
- The real Stooq feed (`ceap.paper.feeds_real`, feed name `"stooq"`) exists but is **not enabled**:
  `config/paper.json` stays on the synthetic feed, no Stooq files are in the repository (the cache is
  git-ignored; Stooq blocks scripted downloads and the project does not bypass bot checks), the R5 pseudo-holdout to 2025-12-31 must stay unspent so the feed refuses those rows,
  and the 12-1 signal needs twelve months of history after the holdout. Roughly January 2027 is
  therefore the earliest it could run on real prices.
- It checks **operations and controls** every day (fills, ledger chains, reconciliation, risk
  blocks, the calendar). It says **nothing about strategy profitability**.
- By the formula in LEARN section 18a, telling a true Sharpe of 1 from 0 <!-- source: tests/unit/paper/test_forward_and_ledger.py --> (alpha 0.05, power 0.8)
  needs about **1980 sessions**, roughly eight years.
- Owner decisions: paper trading is enabled after the 0.7.0 tag (D22), done by setting the
  repository variable `PAPER_DAILY_ENABLED=true` on 2026-11-08; no 1.0.0-rc1 until 2 to 4 weeks of
  that log have been reviewed (D21). The first forward session is 2026-11-09, so no forward result
  exists yet (UNKNOWN).
