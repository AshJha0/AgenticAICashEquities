<!-- Not part of the RAG knowledge corpus (data/reference/knowledge/manifest.json lists quant.md only),
     so release notes here never shift retrieval calibration. -->
# Real data (R5) and paper trading (RP), 0.7.0

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

**RP, evidence class synthetic (forward log NOT RUN LIVE).** The paper book fills at the next
close at `|x| p (h + 0.6 sigma sqrt(|x|/ADV)) / 1e4`, capped at 10 % of ADV, with the backtest's own
cost, financing and position-cap code. The hand-checked two-name example in LEARN §18a (checked by
`test_two_name_three_day_ledger_by_hand`) ends day two at equity $1,002,445.07. The feed is
synthetic; the Stooq feed refuses rows up to 2025-12-31 and has no cached data, so the earliest
real-price start is about January 2027. Distinguishing a Sharpe of 1 from 0 needs about 1980
sessions (LEARN §18a), so the forward record tests operations and controls, not profitability.
