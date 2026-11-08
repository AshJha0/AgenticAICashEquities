# Result: 12-1 momentum on an approximately point-in-time S&P 500 — VOID

- Preregistration: [`prereg_momentum_2026-11.md`](prereg_momentum_2026-11.md), committed in `fb9858e`
  before any data was read, SHA-256
  `7ce84a671ffe24484078a15f23cfbf8a4eb8c983982eaffe1b0cb7946a059da4` (LF-normalised bytes).
- Outcome under preregistration section 7: **VOID**. **No hypothesis-test statistic was computed and
  no verdict is issued.** Items 5.6 (primary test) and 5.7 (pseudo-holdout look) were **not run**.
- Pseudo-holdout (2024-01-01 to 2025-12-31): **UNSPENT**. No price in that range was downloaded or
  read; the loaders still refuse any date after 2023-12-31.

## Why the study is void

Gates are evaluated in order; the first failure is the reported outcome.

1. **Gate 1, coverage: failed.** Usable name-months over member name-months, 2005-01 to 2023-12,
   was **75.1 %** against a required 95 %. The worst month was 53.9 % (December 2004 formation);
   coverage rises from about 55 % in 2005 to 95 % in 2023 (`coverage_report.csv`). The cause is
   survivorship in the free source: yfinance does not serve history for most delisted or acquired
   companies (for example LEH, CELG, ESRX, and EA after it was taken private), so the further back
   the month, the more of that month's actual index members are missing. A momentum test on the
   remaining names would be a test on a survivor-tilted universe, which is what the preregistration
   was written to rule out.
2. **Gate 2, Stooq vs yfinance return cross-check: not run.** Stooq's download endpoint now answers
   scripted requests with a JavaScript proof-of-work browser check. The project does not solve or
   bypass bot checks (decision D2), and the owner decided against a manual browser download. Under
   the preregistration a gate that cannot be evaluated counts as failed.
3. Gate 3 was evaluated (below) but cannot rescue the study: gates 1 and 2 fail first.

## What passed (descriptive pipeline validation only)

These numbers show the pipeline builds the intended portfolio. They are not evidence for or against
H1, and they were computed on the same coverage-deficient sample.

| Check | Result |
|---|---|
| Correlation of the gross decile long-short with Ken French UMD, 228 months | 0.869 (gate 3 threshold 0.5) |
| FF5 + UMD regression | R² 0.77; UMD loading 1.41 |
| Quality gate: hard-fail series (duplicate, unordered or off-calendar dates; failed known split) | 0 of 613 |
| Known splits AAPL 2014-06-09 (7:1) and 2020-08-31 (4:1) absorbed by the adjusted series | pass, pass |
| Survivor-only universe vs point-in-time, gross | correlation 0.93 |

Full figures: `quality_report.json`, `replication_report.json`.

**Disclosure.** Plan item 5.5 required a breakeven-cost figure. It came out at **-38 bps** one way,
which can only happen when the mean gross return is negative. That figure therefore partly reveals
the sign of gross returns on this sample. It was computed before the void decision, as the plan
required, and it is reported here rather than withheld. It is not the preregistered statistic (that
is a Newey-West t on *net* returns), it comes from a sample that fails the coverage gate, and it
supports no claim about momentum.

## Claims

Allowed: "The preregistered test could not be run on free data: point-in-time coverage was 75.1 %
against a required 95 %, and the second-vendor cross-check was unavailable."

Forbidden: any statement that momentum does or does not work on this universe, any Sharpe ratio,
PROMOTE, alpha, tradable, any dollar capacity.

## What would un-void a future attempt

- Delisting-aware daily prices with delisting returns (CRSP-like; generally paid, which the zero-spend
  rule currently excludes), or
- a free source whose coverage of delisted and acquired members reaches 95 % of member name-months
  over the evaluation window, plus a second source for the return cross-check.

Any new attempt needs a **new, separately dated and hashed preregistration**. This one is closed:
its in-sample data has been looked at (gross book, replication and breakeven), so reusing its
specification on a better data set would no longer be a clean preregistered test of that sample.
The pseudo-holdout remains unspent and could serve as genuinely unseen data for a new
preregistration.
