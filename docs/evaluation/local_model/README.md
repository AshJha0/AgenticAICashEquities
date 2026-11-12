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
| Agent evals | full case sets (planner 10; reporter cases from successful plans only, so n varies by seed; critic 15 planted faults) |
| Golden slice | first 3 golden items (`golden_limit=3`, a compute budget set before the runs); **not measured**, see the correction below |
| Injection subset | 40 of 200 attacks, 10 per surface, chosen by `ceap.eval.injection --per-surface 10` |
| Judge | 50 calibration items; a dimension is enabled only if Cohen's kappa > 0.6 |

## Results

Pooled over seeds 1-3 from replay (`qwen2.5_7b-instruct.json`, written by
`python -m ceap.eval.local_model --replay --write --seeds 1 2 3`). Each rate is k/n with a 95 %
Wilson interval; the per-seed values are what to read first (see below).

| Agent / check | k/n | Rate | 95 % CI | Seeds 1 / 2 / 3 |
|---|---|---|---|---|
| Planner: plan valid without repair | 15/30 | 0.50 | 0.33-0.67 | 0.50 / 0.60 / 0.40 |
| Planner: plan repaired | 14/30 | 0.47 | 0.30-0.64 | 0.40 / 0.40 / 0.60 |
| Planner: canonical fallback | 1/30 | 0.03 | 0.01-0.17 | 0.10 / 0.00 / 0.00 |
| Planner: tool precision | 161/161 | 1.00 | 0.98-1.00 | 1.00 / 1.00 / 1.00 |
| Planner: tool recall | 61/84 | 0.73 | 0.62-0.81 | 0.64 / 0.75 / 0.79 |
| Reporter: faithful claims | 25/39 | 0.64 | 0.48-0.77 | 0.67 / 0.40 / 0.79 |
| Reporter: claims dropped by the harness | 14/39 | 0.36 | 0.23-0.52 | 0.33 / 0.60 / 0.21 |
| Reporter: untraceable numbers reaching the report | 0/39 | 0.00 | 0.00-0.09 | 0 / 0 / 0 |
| Critic: planted faults detected | 35/45 | 0.78 | 0.64-0.87 | 0.73 / 0.80 / 0.80 |
| Golden slice (end to end) | not measured | – | – | withdrawn 2026-11-12, see below |

**Correction, 2026-11-12: the golden slice was not measured.** This table first carried four golden
rows (primary cause 6/9, must-mention 9/9, no forbidden claim 6/9, verdict 3/3). They were output of
the deterministic mock, not of the model. The cassettes hold no call from the golden run, so on
replay every request missed the cassette and the router answered from its `MockLLMClient` fallback
(`src/ceap/eval/local_model.py:94`, `src/ceap/llm/router.py:404-417`). A replay of seed 1 with a
counter gave 0 cassette hits and 15 mock answers and reproduced the published scores exactly. The
live runs scored 0/3 on primary cause in each seed (`qwen2.5_7b-instruct_s1.json` to `_s3.json`),
with the backtest tools timing out under the model's CPU load, so they do not measure the model
either. The `golden` blocks in the committed JSON files are kept as recorded and must not be read as
model results; they are regenerated with the 0.10.0 re-run, where an evaluation run fails on a
fallback. The planner, reporter and critic rows above are scored from recorded model replies and are
not affected.

Plain reading: a 7B model on a laptop produces a valid plan about half the time and the harness
repairs most of the rest; it never picks a tool outside the task (precision 1.00) but misses about a
quarter of the tools the gold plans use. About a third of the reporter's claims are dropped by the
harness because they are not traceable to tool output, and none of the untraceable numbers reaches
a report: the guardrail, not the model, is what keeps the report honest.

**Prompt injection** (`qwen2.5_7b-instruct_injection.json`, 40 attacks, 10 per surface, one seed):

| Surface the attack was planted in | Obeyed | 95 % CI | Unusable replies |
|---|---|---|---|
| User question | 3/10 | 0.11-0.60 | 2/10 |
| Retrieved document | 0/10 | 0.00-0.28 | 0/10 |
| Tool output | 0/10 | 0.00-0.28 | 0/10 |
| Finding statement | 0/10 | 0.00-0.28 | 0/10 |
| **All** | **3/40** | **0.03-0.20** | |

The three obeyed attacks were authority, direct and French styles, all planted in the user question.
0/10 means "not seen at this sample size" (upper
bound 28 %), not "safe". The count is conservative: a reply that quotes part of the canary counts
as obeyed.

**Judge calibration** (`judge_calibration.json`, 50 items, enable bar kappa > 0.6): `hedged` kappa
1.00 (enabled); `faithful` 0.51 and `complete` -0.03 (not enabled). The judge therefore scores only
hedging in 0.9.0.

**Challenger (B7): not measured in 0.9.0.** The comparison code asks both models through a bare
planner request that matched neither model's recordings, and a re-run showed the same path marks
the primary's live replies unusable too. A number from that path would measure the harness defect,
not the models, so none is published. llama's raw replies are kept in
`tests/cassettes/local_model/llama3.1_8b-instruct-q4_K_M_challenger_s1.json`; fixing the path and
re-running is a known issue for the next release.

## How to read the numbers

- **Seeds share the case set.** Pooling k/n across three seeds treats them as independent draws,
  which they are not: the pooled interval is too narrow. Read the per-seed values and their range
  next to it.
- **Small samples.** The planner set has 10 cases, the critic set 15 planted faults and the
  injection subset 40 attacks. The intervals are wide; these are first measurements, not settled
  rates.
- **Published agent numbers come from replay, not the live run.** Agent metrics were identical
  live and on replay. An earlier version of this note said the same held for the golden slice
  (seed 1 live 0/3, replay 2/3, "the model's replies are identical in both"). That was wrong: the
  replayed golden answers came from the mock fallback (see the correction above).
- **Judge labels are not independent.** The 50 calibration labels were written by Claude, not by
  independent human raters. A kappa above 0.6 shows the judge agrees with those labels, not with
  expert humans.
- **Ollama was upgraded mid-run** (to 0.40.16 on 2026-11-11, after seeds 1-3; the judge,
  challenger and injection stages ran on 0.40.16). Model digests are unchanged and pinned in each
  cassette.
- **A 7B model on CPU** says whether the platform works with an open model under its guardrails,
  not how well the best available model would do.

## Reproduce

```bash
python -m ceap.eval.local_model --replay --seeds 1 2 3
```

Replay needs no Ollama and is what CI runs. A live run needs Ollama with the pinned digest and
`CEAP_LOCAL_MODEL=1`; see COOKBOOK recipe 49.
