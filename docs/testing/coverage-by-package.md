# Branch coverage by package

Floor: 90 % per package, 95 % for `analytics` and `domain`. Figure = (covered lines + covered
branches) / (statements + branches), computed by `scripts/coverage_by_package.py`. Packages under
their floor are listed in `BASELINE` in the script (the measured figure less 0.1 point): CI fails if
any of them drops further, or if any other package falls below its floor.

## Measurement (2026-11-14, after lane W2)

`pytest -m "not nightly" tests/unit --cov=ceap --cov-branch` on the **unit tier only**, run in two
halves with `--cov-append` (976 tests). CI measures the whole PR tier (agent, integration, contract,
evaluation tests too), so its figures are higher, notably for the top-level modules (`drift.py`
and the `evaluation.py` runners are exercised in the contract and evaluation tiers).

Eight of nineteen packages are still under their floor. Three failures in that run are not caused by
these tests and lower the figure slightly: `test_overfit_flag` and
`test_stage_orders_tool_checks_and_halts` fail on the base branch, and `test_kill_9_mid_run_day_converges`
failed under machine load; the last one is why `paper` reads 83.8 % here against its 84.0 % baseline,
which is kept.

| Package | Statements | Branches | Branch coverage | Floor | Status |
|---|---:|---:|---:|---:|---|
| `agents` | 1853 | 600 | 81.1 % | 90 % | under floor (baseline) |
| `analytics` | 3577 | 788 | 89.2 % | 95 % | under floor (baseline) |
| `api` | 966 | 218 | 91.6 % | 90 % | ok |
| `ceap (top-level)` | 1091 | 222 | 79.3 % | 90 % | under floor (baseline) |
| `data` | 2259 | 498 | 90.7 % | 90 % | ok |
| `domain` | 987 | 58 | 95.7 % | 95 % | ok |
| `eval` | 821 | 158 | 73.1 % | 90 % | under floor (baseline) |
| `execution` | 1086 | 260 | 92.4 % | 90 % | ok |
| `harness` | 1708 | 494 | 78.2 % | 90 % | under floor (baseline) |
| `llm` | 1399 | 422 | 74.8 % | 90 % | under floor (baseline) |
| `mcp` | 1235 | 210 | 84.4 % | 90 % | under floor (baseline) |
| `ml` | 560 | 98 | 95.7 % | 90 % | ok |
| `observability` | 361 | 98 | 96.5 % | 90 % | ok |
| `paper` | 1524 | 472 | 83.8 % | 90 % | under floor (see note) |
| `persistence` | 753 | 182 | 95.9 % | 90 % | ok |
| `policy` | 530 | 184 | 96.2 % | 90 % | ok |
| `rag` | 698 | 182 | 93.8 % | 90 % | ok |
| `studies` | 258 | 54 | 99.7 % | 90 % | ok |
| `util` | 135 | 62 | 91.9 % | 90 % | ok |

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
