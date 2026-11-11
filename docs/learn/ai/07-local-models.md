# Lesson 7: Local models

Learning goals: run an open-weight model through Ollama in principle; explain why a model is pinned
by digest; explain cassettes and offline replay; summarise what a 7B CPU model did on CEAP.

## Plain idea

A model on your own machine costs nothing per call and sends no data out, but it is slow and weaker
than a hosted frontier model. To make its results reproducible, CEAP pins the exact model weights
by digest and records every request and reply in a cassette. CI and students replay the cassette:
the same replies, no Ollama, no key.

## Formal statement

- Pin: a local model is identified by name plus the content digest Ollama reports
  (`ceap.llm.ollama.model_digest`), recorded in every cassette's provenance block; a tag alone can
  move. (`src/ceap/llm/model_pins.toml` pins the hosted model ids, not the local ones.)
- Cassette (`ceap.llm.recording`): a list of interactions keyed by a hash of the canonical request
  body, with nonces masked, plus provenance (model digest). Replay serves the recorded reply for a
  matching request.
- Live run: Ollama with the pinned digest and `CEAP_LOCAL_MODEL=1` (COOKBOOK recipe 49).

## Worked example from this repo

From [docs/evaluation/local_model/README.md](../../evaluation/local_model/README.md): primary
`qwen2.5:7b-instruct` on one CPU-only laptop at roughly 5 output tokens/s, temperature 0.7, seeds 1-3.

| Check | k/n | Rate |
|---|---|---|
| Plan valid without repair | 15/30 | 0.50 |
| Reporter faithful claims | 25/39 | 0.64 |
| Untraceable numbers reaching the report | 0/39 | 0.00 |
| Critic planted faults detected | 35/45 | 0.78 |
| Injection obeyed (all surfaces) | 3/40 | CI 0.03-0.20 |

Ollama was upgraded mid-run (to 0.40.16 after seeds 1-3); digests were unchanged and are pinned in
each cassette. A 7B CPU model shows the platform works with an open model under its guardrails, not
how well the best model would do.

In CEAP:
- `src/ceap/llm/ollama.py`, `pins.py`, `model_pins.toml`, `recording.py`.
- `src/ceap/eval/local_model.py`.
- `tests/cassettes/local_model/`.

## Offline exercise

```bash
python -m ceap.eval.local_model --replay --seeds 1
```

About 95 s on the test laptop. Then open one file in `tests/cassettes/local_model/`, find the
provenance block and the model digest. If you have Ollama, compare it with the `digest` that
`ollama list` (or `/api/tags`) reports for the same model: a different digest means a different
model, even under the same tag.

## Pitfalls

- Pinning by tag instead of digest.
- Publishing live numbers that were affected by tool timeouts under CPU load.
- Reading a 7B result as a statement about hosted models.

## Self-check

<details><summary>1. Why does replay need no Ollama?</summary>
The cassette holds the recorded replies; replay matches each request to its recorded reply.</details>

<details><summary>2. Why mask nonces in the match key?</summary>
Nonces are random per run, so an unmasked key would never match on replay.</details>

<details><summary>3. What did the upgrade of Ollama mid-run change?</summary>
The server version for later stages; model digests stayed the same.</details>

## Further reading

- [COOKBOOK.md](../../../COOKBOOK.md), recipe 49.
- [docs/ai-agents/ai-agents.md](../../ai-agents/ai-agents.md), "A local open-weight model (0.9.0)".
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
