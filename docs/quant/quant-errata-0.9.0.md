# quant.md errata (0.9.0)

`docs/quant/quant.md` is part of the RAG knowledge corpus (`data/reference/knowledge/manifest.json`).
Its bytes are pinned: the corpus pin, the committed dense and hybrid retrieval caches, the
preregistered 0.9.0 RAG results and the hand-built report cassettes were all measured against
it. Editing it moves every one of those, so corrections found after measurement live here until
the next corpus re-measurement (planned for 0.10.0), when they are folded back in. This page is
kept out of the RAG corpus.

The 0.9.0 docs audit (2026-11-12) checked `quant.md` against the code and the committed results.
Where the two disagree, the code and the data are right and the page below is the correction.

## 1. The PROMOTE hurdle is fixed in advance; the null sweep measures it

**What quant.md says:** the hurdle is "measured, not chosen": the lowest `t` on a 0.05 grid whose
Clopper-Pearson 95 % upper bound is at most 5 %, which gives **0.40**.

**Correct since 0.8.0 (plan step D3):** the in-sample IC t hurdle is set before measuring:
`3.0` for a result chosen from a search (the default 24-configuration flow) and `1.645`
(one-sided 5 %) when exactly one preregistered trial counts (Harvey, Liu and Zhu 2016;
`MINED_HURDLE` and `SINGLE_TEST_HURDLE` in `ceap.analytics.research_assessment`).
`ceap evaluate --suite null --seeds 500` still runs all 24 configurations on 500 pure-noise
datasets and reports the false-PROMOTE rate, with its Clopper-Pearson band, across a grid of
hurdles. Until 0.7.1 the hurdle was read off that curve.

| | Value |
|---|---|
| In-sample IC t hurdle (a priori) | **3.0** for a search, 1.645 for one preregistered test |
| False PROMOTEs at 3.0 | 0 / 500, 95 % upper bound 0.74 % |
| Lowest hurdle with upper bound <= 5 % (`lowest_hurdle_with_upper_le_5pct`) | 0.40, the 0.7.1 hurdle, where 15 / 500 = 3.0 % were false |

Why the change: a hurdle read off the sweep is itself fitted to the noise set. The price is
power: at `t >= 3` the realistic premia in the power table are detected far less often than at
0.40. The `NO_ALPHA` flag row should read "in-sample non-overlapping IC t below the a-priori
hurdle (3.0, or 1.645 for one preregistered trial), fewer than 30 periods, or deflated Sharpe
< 0.5".

## 2. The INCONCLUSIVE verdict

**What quant.md says:** the verdict is `PROMOTE` or `REJECT`.

**Correct since 0.8.0 (D4):** the verdict is `PROMOTE` with no flag and `REJECT` otherwise,
except that it is `INCONCLUSIVE` when every flag is statistical (`NO_ALPHA` or `OOS_DECAY`) and
the 95 % interval of the full-period net Sharpe, with the Lo (2002) standard error in its daily
form, covers both 0 and 0.5. With `SR_d = SR / sqrt(252)` the daily Sharpe and `T` the number of
days, `SE(SR) = sqrt((1 + SR_d^2/2) / T) * sqrt(252)` (Lo 2002, eq. 9, annualised under iid).

Worked example: a net Sharpe of 0.3 over 5 years (1,260 days) has SE
`sqrt((1 + 0.000179) / 5) = 0.447`, so the interval is [-0.58, 1.18]. It covers both 0 and 0.5, so
the verdict is INCONCLUSIVE, not REJECT. (Corrected in 0.10.0, item Q1, finding R-15: this page
and the code before 0.10.0 put the annual Sharpe in the squared term, `sqrt(1.045 / 5) = 0.457`,
which is about 22 % too wide at Sharpe 1 and 73 % at Sharpe 2.)

## 3. Power table figures

The committed power file was measured at the 0.7.1 hurdle of 0.40 (its `hurdle` field), not the
current 3.0. Its figures are:

| Premium (bps a year, cross-sectional sd) | 0 | 600 | 900 | 1200 | 1500 | 1800 | 2400 |
|---|---|---|---|---|---|---|---|
| Expected in-sample t | 0.0 | 0.5 | 1.1 | 2.0 | 3.0 | 4.1 | 6.7 |
| Predicted detection | 5 % | 13 % | 29 % | 54 % | 78 % | 93 % | 100 % |
| Observed detection (40 seeds) | 15 % | 20 % | 38 % | 60 % | 80 % | 93 % | 100 % |

(quant.md shows 4/11/26/52/77 % predicted and 13/20/35 % observed for the first three points.)
The closed form is slightly conservative (realised t runs about 15 to 20 % above expectation);
the zero-premium point (6 of 40 seeds against an expected 2.2) is the one to watch. The null
false-PROMOTE rate at 3.0 is 0/500.

**Since 0.10.0 (Q5, finding R-19)** `docs/quant_power_curve.json` is regenerated at both hurdles in
force (1.645 and 3.0); the figures above are history. See the power section in
`docs/research/README.md` for the detectable premium.

## 4. Research scenario suite score

**What quant.md says:** 23/24 clear and 6/6 near-threshold, 29/30 overall.

**Correct since the 0.8.0 recalibration:** the mock-model pipeline scores **25/30**: 23/24
clear and 2/6 near-threshold (`latest.json`, accuracy 0.8333; see
`docs/evaluation/evaluation.md`). 29/30 was the score at the 0.40 hurdle.

## 5. Two known numerical issues (open in 0.9.0)

Both come from guards that test for an exact zero where a tolerance is needed.

- `ceap.analytics.risk_research.factor_regression` returns `r2 = 1 - SSE/TSS` only when
  `TSS > 0`. For a constant `y`, `TSS` is a rounding residue (about 1e-31 for `y = 0.3` over 120
  months), not 0, so the guard passes and `r2` is noise: a check with two random factors gave
  anything from -3.3 to 0.82 depending on the constant. The correct answer is "undefined" (NaN).
- `ceap.analytics.portfolio_risk.raw_betas_ex_self` skips a name only when `sxx <= 0`. When the
  market excluding the name is constant up to rounding, `sxx` is tiny but positive and the beta
  explodes (a flat four-name market gave a beta of about 4e15). The Vasicek shrinkage then pulls
  the name close to 1, but the raw beta is still reported.

The fix for both is a relative tolerance (for example `TSS <= 1e-12 * n * mean(y)^2`), planned
for 0.10.0.
