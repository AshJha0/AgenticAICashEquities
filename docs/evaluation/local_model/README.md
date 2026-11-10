# Local open-weight model evaluation (0.9.0)

Evidence class: **local model**: real model calls to an open-weight model running on one laptop
through Ollama, recorded as cassettes and replayed offline in CI. Not a hosted frontier model, not a
human-labelled benchmark.

## The idea

Until 0.9.0 every agent evaluation ran on mocks or hand-built cassettes. Those test the harness
(plans are validated, numbers are traced, governance holds), not whether a real model can do the
work. Here a real 7B model plays every LLM role (planner, reporter, critic) and the same scorers
measure it.

## Setup, fixed before any run

| Item | Value |
|---|---|
| Primary model | `qwen2.5:7b-instruct` (digest in each cassette's provenance) |
| Challenger | `llama3.1:8b-instruct-q4_K_M` |
| Hardware | one CPU-only laptop, roughly 5 output tokens/s |
| Sampling | temperature 0.7, seeds 1, 2, 3 |
| Agent evals | full case sets (planner 10, reporter 15, critic 15 planted faults) |
| Golden slice | first 3 golden items (`golden_limit=3`, a compute budget set before the runs) |
| Injection subset | 40 of 200 attacks, 10 per surface, chosen by `ceap.eval.injection --per-surface 10` |
| Judge | 50 calibration items; a dimension is enabled only if Cohen's kappa > 0.6 |

## Results

TBD-LOCAL-MODEL-RESULTS: table filled from `qwen2.5_7b-instruct.json` (pooled, written by
`python -m ceap.eval.local_model --replay --write --seeds 1 2 3`), the per-seed `_s<N>.json` files,
`judge`, `challenger` and `injection` outputs. Each rate is shown as k/n with a 95 % Wilson
interval, plus the per-seed values.

## How to read the numbers

- **Seeds share the case set.** Pooling k/n across three seeds treats them as independent draws,
  which they are not: the pooled interval is too narrow. Read the per-seed values and their range
  next to it.
- **Small samples.** The golden slice has 3 items and the injection subset 40 attacks. The
  intervals are wide; these are first measurements, not settled rates.
- **Published numbers come from replay, not the live run.** During the live runs the backtest
  tools timed out under the model's own CPU load, which marked some golden cases incomplete (seed
  1 live: golden cause 0/3; replay of the same cassette: 2/3). The model's replies are identical
  in both; only the deterministic tools differ. Replay runs the tools without that load, so it is
  the measurement of record. Agent metrics were identical live and on replay.
- **Judge labels are not independent.** The 50 calibration labels were written by Claude, not by
  independent human raters. A kappa above 0.6 shows the judge agrees with those labels, not with
  expert humans.
- **A 7B model on CPU** says whether the platform works with an open model under its guardrails,
  not how well the best available model would do.

## Reproduce

```bash
python -m ceap.eval.local_model --replay --seeds 1 2 3
```

Replay needs no Ollama and is what CI runs. A live run needs Ollama with the pinned digest and
`CEAP_LOCAL_MODEL=1`; see COOKBOOK recipe 49.
