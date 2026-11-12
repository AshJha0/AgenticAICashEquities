# ADR 0005: A study that fails a preregistered gate is VOID and publishes no statistic

- Status: Accepted (0.7.0). Recorded 0.9.0.

## Context

The R5 momentum study on free data could not reach the data coverage its preregistration required
(75.1 % against 95 %, `docs/research/result_momentum_2026-11.md`). Computing the test statistic
anyway would give a number on a survivor-tilted universe, and once a number exists it gets quoted.
Relaxing the gate after seeing the coverage would make the preregistration meaningless.

## Decision

Data-quality gates are evaluated in the preregistered order before any test statistic. The first
failed gate, or a gate that cannot be evaluated, makes the outcome **VOID**: no hypothesis-test
statistic is computed, no verdict is issued, and the holdout stays unspent. The result file says
VOID in its title and gives the gate numbers.

## Consequences

- Some studies end with nothing to report but their gates. R5 (0.7.0, 75.1 % coverage) and R5 v2
  (0.7.1, 74.9 %) both did.
- The holdout survives a VOID study, so a later study with better data can still use it once.
- Docs must carry VOID through: `docs/evaluation/evaluation.md` and `docs/DIAGRAMS.md` show it.

## Evidence

- `docs/research/prereg_momentum_2026-11.md` section 7 (gate order) and the two result files
  (`result_momentum_2026-11.md`, `result_momentum_2026-11-v2.md`).
- The loaders refuse dates inside the holdout window.
