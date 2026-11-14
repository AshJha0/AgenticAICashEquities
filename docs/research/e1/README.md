# E1 result: 49-industry momentum, preregistered (0.9.0 WS-E)

**Verdict: NULL.** The preregistered test did not reject H0 in the 1990-2015 holdout (one-sided
Newey-West t = 1.16 < 1.645 on net returns at 10 bps per side); the design could detect an
annualised Sharpe of about 0.49 with 80 % power. Replication not verified (gate 1 NOT RUN, as stated
in advance). This does not show that industry momentum does not exist.

## Plain idea, then the rule

Each month buy the 7 Ken French industries with the best 6-month return, short the 7 worst, hold a
month. The rules (windows, test, costs, gates, allowed claims) were fixed in
[`prereg_industry_momentum_2026-11.md`](prereg_industry_momentum_2026-11.md), hash-locked in
[`prereg_lock.json`](prereg_lock.json) and committed (2631118) before any return was downloaded.
Signed by Claude under delegated owner decision D3.

## Order of events (git history is the evidence)

1. 2631118 prereg + lock. 2. f10c631 code, tests and raw-file SHA-256 ([`data_hashes.json`](data_hashes.json)).
3. df4757d in-sample result committed ([`result_in_sample.json`](result_in_sample.json)).
4. Holdout opened 2026-11-09T18:06Z ([`holdout_ledger.json`](holdout_ledger.json)). That run crashed in
   the *live*-window H2 regression (no DOWN month after 2016, singular matrix) after the ledger write
   and before any holdout number was written or printed. The bug was fixed (H2 now reports NOT
   TESTABLE for a one-state window, test added) and the same opening was completed once through a
   ledgered `crash_rerun` entry. No construction choice changed. Disclosed here as a deviation.
5. Holdout and live results: [`result_holdout.json`](result_holdout.json).

## Numbers (evidence: the JSON files above; Ken French data, file hashes recorded)

| Window | Months | Gross Sharpe | Net10 Sharpe [95 % CI] | Net10 NW t (lag) | Net20 t | Breakeven bps/side | Turnover/month |
|---|---|---|---|---|---|---|---|
| In-sample 1927-1989 | 756 | 0.35 | 0.26 [-0.06, 0.63] | 1.86 (3) | 1.22 | 39.3 | 1.36 |
| **Holdout 1990-2015** | 312 | 0.32 | 0.23 [-0.12, 0.59] | **1.16 (1)** | 0.74 | 38.0 | 1.40 |
| Live 2016-2026-09 (descriptive) | 129 | 0.16 | 0.06 [-0.57, 0.71] | 0.20 (1) | -0.13 | 16.0 | 1.47 |

Secondary (do not change the verdict):

- Alpha t (gross): in-sample FF3+UMD 0.85, FF5+UMD (1963-07 on) 1.04; holdout FF5+UMD 0.36.
  UMD loadings 0.66-0.93: most of the return is ordinary stock momentum.
- H2 market state (Cooper, Gutierrez and Hameed 2004): in-sample mean UP 0.88 %/month, DOWN -1.82 %,
  t(b_DOWN) = -3.04, **rejected** (as predicted). Holdout UP 0.75 %, DOWN -0.47 %, t = -1.13, not
  rejected. Live: NOT TESTABLE (no DOWN month).
- Gates: (1) replication NOT RUN (no free published table for this construction; stated in the
  prereg). (2) ETF-sector gate **PASS**: median correlation 0.94 over 1999-2015 (XLV lowest, 0.70).
  (3) breadth: no month dropped.

## Worked example of the cost line

Holdout mean gross 0.53 %/month, turnover 1.40: at 10 bps per side net = 0.53 % - 0.10 % x 1.40 =
0.39 %/month; breakeven = 0.53 % / 1.40 = 38 bps per side.

## Allowed claim (from section 8, NULL)

"The preregistered test did not reject H0 in the 1990-2015 holdout (t = 1.16); the design could
detect an annualised Sharpe of about 0.49 with 80 % power." Replication not verified.

## Reproduce

```bash
python scripts/fetch_kf_industries.py     # free, no sign-up; rewrites data_hashes.json (git diff shows a vendor change)
python scripts/run_e1_industry_momentum.py  # in-sample only; refuses cached files whose hash differs from data_hashes.json
```
The holdout cannot be reopened: `--open-holdout` refuses because the ledger records the opening.
Code: `src/ceap/studies/industry_momentum.py`; tests: `tests/unit/test_industry_momentum.py`.
