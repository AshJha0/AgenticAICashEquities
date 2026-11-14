# Branch coverage by package

Measured once on 2026-11-14 for 0.10.0 item E1 (finding C-P2-1) with
`pytest -n 4 -m "not nightly" tests/unit --cov=ceap --cov-branch` (825 passed). This is the
**unit tier only**; CI measures the whole PR tier, which also runs the agent, integration and
contract tests, so its figures are higher. Figure = (covered lines + covered branches) /
(statements + branches), computed by `scripts/coverage_by_package.py`.

Floor: 90 % per package, 95 % for `analytics` and `domain`. **Eleven of nineteen packages are under
their floor on this measurement**: agents, analytics, api, the top-level modules, eval, harness,
llm, mcp, observability, paper and studies. They are listed in `BASELINE` in the script at the
figure measured here less 0.1 point: CI fails if any of them drops further, or if any other package
falls below its floor. Raising them is open work, not done in E1.

| Package | Statements | Branches | Branch coverage | Floor | Status |
|---|---:|---:|---:|---:|---|
| `agents` | 1853 | 600 | 81.1 % | 90 % | under floor (baseline) |
| `analytics` | 3547 | 780 | 89.1 % | 95 % | under floor (baseline) |
| `api` | 966 | 218 | 55.0 % | 90 % | under floor (baseline) |
| `ceap (top-level)` | 1091 | 222 | 68.7 % | 90 % | under floor (baseline) |
| `data` | 2259 | 498 | 90.7 % | 90 % | ok |
| `domain` | 986 | 58 | 95.7 % | 95 % | ok |
| `eval` | 798 | 156 | 72.4 % | 90 % | under floor (baseline) |
| `execution` | 1086 | 260 | 92.4 % | 90 % | ok |
| `harness` | 1685 | 488 | 77.7 % | 90 % | under floor (baseline) |
| `llm` | 1402 | 422 | 74.7 % | 90 % | under floor (baseline) |
| `mcp` | 1202 | 200 | 73.8 % | 90 % | under floor (baseline) |
| `ml` | 560 | 98 | 95.7 % | 90 % | ok |
| `observability` | 361 | 98 | 78.0 % | 90 % | under floor (baseline) |
| `paper` | 1523 | 472 | 84.1 % | 90 % | under floor (baseline) |
| `persistence` | 753 | 182 | 95.5 % | 90 % | ok |
| `policy` | 530 | 184 | 96.2 % | 90 % | ok |
| `rag` | 697 | 182 | 93.6 % | 90 % | ok |
| `studies` | 258 | 54 | 44.6 % | 90 % | under floor (baseline) |
| `util` | 129 | 62 | 91.6 % | 90 % | ok |
