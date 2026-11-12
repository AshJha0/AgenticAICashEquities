# Lesson 5: Classical ML

Learning goals: describe the change-point detector (robust z-scores, CUSUM, PELT onset); define
Brier score and expected calibration error (ECE); read a reliability diagram; explain why an alert
needs a calibrated probability.

## Plain idea

Not all AI here is a language model. The engineering and market agents use a small statistical
model to say "latency changed at 14:00" with a probability attached. An alert that says 0.93 should
be right about 93 % of the time; if it is right 99.5 % of the time, the number misleads the person
deciding whether to page someone. Calibration fixes the mapping from score to probability.

## Formal statement

- Change points (`ceap.ml.anomaly`): seven per-minute features become robust z-scores against a
  60-minute reference (median, 1.4826 x MAD). A two-sided CUSUM with allowance `k = 1` alarms when it
  crosses `h`; the onset is the start of the PELT segment (L2 cost, penalty `2 ln n`) containing the alarm.
- Brier score: `(1/n) sum (p_i - y_i)^2`, lower is better.
- ECE: bin predictions by confidence; `sum_b (n_b / n) |mean confidence_b - observed rate_b|`.
- Reliability diagram: observed rate against mean confidence per bin; a calibrated model lies on the diagonal.

Source: [docs/ml/README.md](../../ml/README.md).

## Worked example from this repo

`h` was fitted at 11: 0/300 false alarms on clean sessions (95 % upper bound 1.2 %), mean delay 1.8
minutes ([docs/ml/README.md](../../ml/README.md)). Calibration, from `capability.calibration` in
[docs/evaluation/latest.json](../../evaluation/latest.json) (fitted on 600, evaluated on 603 held out):

| Held-out | Raw | Calibrated |
|---|---|---|
| Brier | 0.01098 | 0.00941 |
| ECE | 0.0675 | 0.0042 |

[docs/ml/reliability_table.md](../../ml/reliability_table.md) shows why: in the 0.9-1.0 bin the raw
model's mean confidence was 0.9302 against an observed 0.9950 (under-confident); after calibration
it was 0.9942 against 0.9900. The diagram is [reliability.svg](../../ml/reliability.svg). Every model
is fitted to synthetic data and labelled with the generator hash.

In CEAP:
- `src/ceap/ml/anomaly.py`: the detector and `tune_threshold`.
- `src/ceap/ml/registry.py`: model hashes, `generator_hash()`, refusing a mismatched model.
- [docs/ml/model_cards.md](../../ml/model_cards.md).

## Offline exercise

```bash
python -m pytest -q -p no:randomly --no-cov tests/unit/ml/test_registry.py
```

Then compute the raw ECE by hand from the three "raw" rows of the reliability table and
compare with `latest.json` (the table shows only non-empty bins).

## Pitfalls

- Reading a raw classifier score as a probability.
- Judging calibration on the data used to fit it; CEAP reports the held-out set.
- Forgetting the model is fitted to synthetic data: the label says so on every output.

## Self-check

<details><summary>1. What does the 0.9302 vs 0.9950 raw bin say?</summary>
The raw model was under-confident: it was right more often than it claimed.</details>

<details><summary>2. Why prefer Brier and ECE together?</summary>
Brier mixes calibration and sharpness; ECE isolates calibration error.</details>

<details><summary>3. How was the threshold h chosen?</summary>
Lowest expected cost per session, subject to the 95 % upper bound on the false-alarm rate being at most 1/20.</details>

## Further reading

- [LEARN.md](../../../LEARN.md), section 14.
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
