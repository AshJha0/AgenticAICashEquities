# Lesson 6: Honest AI evaluation

Learning goals: explain what a golden set is and why it is signed off; compute a Wilson interval;
say what a small sample can and cannot claim; explain a kappa-gated LLM judge; say why seeds that
share cases are not independent, why replay is the measurement of record, and why the challenger
was not reported.

## Plain idea

A number about an AI system is only as good as the set it was measured on, the interval around it,
and the honesty about what it does not show. Fix the cases and the rules before you look; report
k/n with an interval; do not pool things that are not independent; and if the measuring path is
broken, publish nothing rather than a number that measures the bug.

## Formal statement

- Wilson 95 % interval for k successes in n: centre `(p + z^2/2n) / (1 + z^2/n)`, half-width
  `z sqrt(p(1-p)/n + z^2/4n^2) / (1 + z^2/n)`, `z = 1.96`. Unlike `p +/- z sqrt(p(1-p)/n)`, it stays
  inside [0, 1] and is not zero-width at 0/n. Code: `ceap.eval.stats.wilson`.
- Cohen's kappa: `(p_o - p_e) / (1 - p_e)`, agreement corrected for chance. Code: `ceap.eval.judge.cohen_kappa`.
- Judge gate: a judge dimension is enabled only if kappa > 0.6 on 50 calibration items.

## Worked example from this repo

From [docs/evaluation/local_model/README.md](../../evaluation/local_model/README.md):

- Judge: `hedged` kappa 1.00 (enabled); `faithful` 0.51 and `complete` -0.03 (not enabled). The
  judge scores only hedging in 0.9.0. The labels were written by Claude, not independent humans, so
  kappa shows agreement with those labels only.
- A fallback can fake a result: the end-to-end golden slice first published for 0.9.0 (primary
  cause 6/9) was answered by the mock fallback, not the model, and was withdrawn on 2026-11-12. An
  evaluation run must fail on a fallback, not continue.
- Seeds share the case set, so the pooled interval is too narrow; read the per-seed values first
  (reporter faithful 0.67 / 0.40 / 0.79).
- Replay versus live: during live runs backtest tools timed out under the model's CPU load (seed 1
  live golden cause 0/3, replay of the same cassette 2/3). Model replies are identical; replay is the
  measurement of record.
- Challenger not measured: the comparison path matched neither model's recordings, so a number would
  measure the harness defect, not the models.

In CEAP:
- `src/ceap/eval/stats.py`, `judge.py`, `judge_reference_v1.json`, `agents.py`, `local_model.py`, `challenger.py`.
- `src/ceap/golden.py` and `tests/golden/`.

## Offline exercise

```bash
python -c "from ceap.eval.stats import wilson; print(wilson(6, 9), wilson(0, 10))"
python -m pytest -q -p no:randomly --no-cov tests/unit/test_r090_judge_enabled.py
```

Check the first output against the table (0.35-0.88 and 0.00-0.28 after rounding).

## Pitfalls

- Reporting a rate without n.
- Pooling seeds over the same cases as if they were independent.
- Trusting an LLM judge on a dimension that never cleared its agreement bar.
- Publishing a number from a path you know is broken.

## Self-check

<details><summary>1. Why is 0/10 not "zero risk"?</summary>
Its Wilson upper bound is 0.28.</details>

<details><summary>2. Which judge dimension was enabled in 0.9.0, and why only that one?</summary>
`hedged`; it was the only dimension with kappa above 0.6.</details>

<details><summary>3. Why is replay, not live, the published result?</summary>
Live runs had tool timeouts from CPU contention; replay keeps the same model replies and runs the tools without that load.</details>

<details><summary>4. What is wrong with pooling three seeds into one interval here?</summary>
The seeds share cases, so the draws are not independent and the interval is too narrow.</details>

## Further reading

- [LEARN.md](../../../LEARN.md), section 14.
- [docs/evaluation/evaluation.md](../../evaluation/evaluation.md).
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
