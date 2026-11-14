# Branch coverage by package

Floor: 90 % per package, 95 % for `analytics` and `domain`. Figure = (covered lines + covered
branches) / (statements + branches), computed by `scripts/coverage_by_package.py`. Packages under
their floor are listed in `BASELINE` in the script (the measured figure less 0.1 point): CI fails if
any of them drops further, or if any other package falls below its floor.

## Measurement (2026-11-14, after lane W9)

`pytest -m "not nightly" tests/unit --cov=ceap --cov-branch` on the **unit tier only**, run in three
parts with `--cov-append` (`-n 2`). CI measures the whole PR tier (agent, integration, contract,
evaluation tests too), so its figures are higher.

Seven of nineteen packages are still under their floor; `eval` now meets it. Four analytics tests
fail on the base branch (`test_overfit_flag`, `test_cost_drag_flag`,
`test_assessment_carries_a_hypothesis_table`, `test_backtest_tool_records_the_gross_in_sample_sharpe`:
the research-assessment flags and hypothesis table changed upstream and the tests were not updated).
Failing tests stop early, which is why `analytics` reads 88.9 % against its 89.1 % baseline; `paper`
reads 83.6 % against 84.0 % under machine load, as in the W2 run. Neither package was touched by
these lanes, so both baselines are kept, not lowered. Statement counts grew since W2 (new code on
the integration branch), which also moved `agents` from 81.1 % to 84.4 % without new tests here.

| Package | Statements | Branches | Branch coverage | Floor | Status |
|---|---:|---:|---:|---:|---|
| `agents` | 2081 | 712 | 84.4 % | 90 % | under floor (baseline) |
| `analytics` | 3754 | 808 | 88.9 % | 95 % | under floor (see note) |
| `api` | 993 | 224 | 91.3 % | 90 % | ok |
| `ceap (top-level)` | 1237 | 250 | 85.6 % | 90 % | under floor (baseline) |
| `data` | 2280 | 506 | 90.8 % | 90 % | ok |
| `domain` | 991 | 58 | 95.7 % | 95 % | ok |
| `eval` | 892 | 184 | 91.3 % | 90 % | ok |
| `execution` | 1094 | 264 | 92.5 % | 90 % | ok |
| `harness` | 1871 | 534 | 80.0 % | 90 % | under floor (baseline) |
| `llm` | 1489 | 448 | 81.2 % | 90 % | under floor (baseline) |
| `mcp` | 1247 | 212 | 85.5 % | 90 % | under floor (baseline) |
| `ml` | 588 | 106 | 95.0 % | 90 % | ok |
| `observability` | 360 | 96 | 98.5 % | 90 % | ok |
| `paper` | 1575 | 486 | 83.6 % | 90 % | under floor (see note) |
| `persistence` | 802 | 208 | 95.8 % | 90 % | ok |
| `policy` | 530 | 184 | 96.2 % | 90 % | ok |
| `rag` | 702 | 184 | 93.8 % | 90 % | ok |
| `studies` | 262 | 56 | 99.7 % | 90 % | ok |
| `util` | 135 | 62 | 93.9 % | 90 % | ok |

## Change in lane W9

| Package | After W2 | After W9 | Tests added |
|---|---:|---:|---|
| `eval` | 73.1 % | 91.3 % | corpus pin check, injection corpus and runner, B3 sensitivity, T5 audit replay, R-01 refusal |
| `ceap (top-level)` | 79.3 % | 85.6 % | drift monitors on synthetic cassettes |
| `llm` | 74.8 % | 81.2 % | model pins, prompt integrity (A15), data blocks (A8), context truncation, cassette match keys |
| `harness` | 78.2 % | 80.0 % | evidence binding, value-at lookup, cited-value tolerance |
| `mcp` | 84.4 % | 85.5 % | none (upstream changes) |

## Change from the first measurement (E1, 825 tests)

| Package | Before | After |
|---|---:|---:|
| `studies` | 44.6 % | 99.7 % |
| `api` | 55.0 % | 91.6 % |
| `ceap (top-level)` | 68.7 % | 79.3 % |
| `observability` | 78.0 % | 96.5 % |
| `mcp` | 73.8 % | 84.4 % |
| `eval` | 72.4 % | 73.1 % |

`mcp` and `eval` had grown since the first measurement (H2 grant signing, H3 screen measurement,
tested only outside the unit tier) and had fallen to 71.6 % and 70.6 %; unit tests now cover them.
