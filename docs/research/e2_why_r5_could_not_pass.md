# E2: why the stock-level R5 study could not pass on free data (process lesson)

**Status: R5 stays closed (VOID).** This page records why, so the same mistake is not repeated.

## Plain idea

A momentum test on S&P 500 stocks needs the prices of every company that was in the index on each
formation date, including the ones that later went bust, were bought or were renamed. Free price
vendors keep mostly the companies that still trade. The missing names are not random: they are the
failures and the takeovers, exactly the names that drive the short leg of momentum. So the study's
own coverage gate (95 % of member name-months) was always going to fail, and that could have been
predicted before writing the preregistration.

## Formal statement

R5 required coverage `C = covered member name-months / all member name-months >= 0.95` over
2005-2023 (`docs/research/prereg_momentum_2026-11.md`, gate 1). For a vendor that lists only names
alive at download, `C` is bounded above by the share of name-months belonging to survivors plus the
share of non-survivors the vendor happens to retain. With roughly 20-25 index changes a year over
19 years, the non-survivor share of member name-months is far above 5 %, so `C < 0.95` follows from
vendor coverage alone, without reading a single price.

## What happened (evidence: project records)

| Attempt | Primary vendor | Coverage | Source |
|---|---|---|---|
| v1, 2026-11-07 | yfinance | 75.1 % | `docs/research/result_momentum_2026-11.md` |
| v2, 2026-11-08 | Stooq bulk (owner hand download) | 74.9 % (75.1 % with yfinance fallback) | `docs/research/result_momentum_2026-11-v2.md` |

Both vendors miss the same delisted and acquired companies; 337 of Stooq's 606 matching series also
start after 2004. No test statistic was computed and the pseudo-holdout is unspent.

## Worked example

Suppose 500 members and 25 changes a year. Over 19 years about 475 names leave the index. If each
leaver was a member for 8 years on average before leaving, leavers contribute about
475 x 96 = 45 600 of the 500 x 228 = 114 000 member name-months, about 40 %. A vendor that keeps
even half of the leavers reaches only about 80 % coverage. The measured 75 % sits right there.
(The 8-year figure is an illustrative assumption, not a measured number.)

## Lesson

Before preregistering a study, check that the data *can* pass its own gates: a desk-check of vendor
survivorship against the universe's turnover costs an hour and would have avoided two attempts.
The 0.9.0 follow-up, E1, therefore moved to a portfolio-level test on data that already handles
delisting (Ken French industry portfolios, built from CRSP with delisting returns):
`docs/research/e1/`.

## What would reopen R5

A delisting-aware stock price source (CRSP-like, usually paid and so excluded by the zero-spend
rule) or a free source covering 95 % of member name-months, under a new preregistration.
