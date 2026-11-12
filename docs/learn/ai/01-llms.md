# Lesson 1: LLMs

Learning goals: say what a token and a sampling temperature are; explain why CEAP asks the model for
JSON that matches a schema; name the three outcomes of a planner call (valid, repaired, canonical
fallback); state the rule that the model never computes or invents a number.

## Plain idea

A large language model reads text as a sequence of tokens (word pieces) and, one token at a time,
predicts a probability for every possible next token, then samples one. Run the same prompt twice
and you can get two different answers. That is useful for writing prose and risky for anything you
would act on, so CEAP uses the model only for the parts where a wrong guess is caught: choosing
which tools to call, and writing a narrative around numbers that code has already computed.

## Formal statement

- Sampling: given logits `z_i` and temperature `T > 0`, the next token is drawn with probability
  `p_i = exp(z_i / T) / sum_j exp(z_j / T)`. Lower `T` concentrates mass on the top token; a fixed
  seed makes a given server and model reproducible, not a different model.
- Structured output: the request carries a JSON schema; a reply is usable only if it parses and
  validates. CEAP treats every reply as one of: valid without repair, repaired (exactly one more
  model call whose target is the list of schema and semantic errors), or unusable, in which case the deterministic `canonical_plan` is used.
- The boundary (`src/ceap/__init__.py`): LLM = reasoning and orchestration assistance; Python
  analytics = deterministic computation. Every number in a report comes from `ceap.analytics` or a
  tool call.

## Worked example from this repo

In 0.9.0 a 7B open-weight model (`qwen2.5:7b-instruct`, temperature 0.7, seeds 1, 2, 3) played the
planner on 10 cases per seed. Source: [docs/evaluation/local_model/README.md](../../evaluation/local_model/README.md).

| Planner outcome | k/n | Rate | 95 % CI |
|---|---|---|---|
| Valid without repair | 15/30 | 0.50 | 0.33-0.67 |
| Repaired | 14/30 | 0.47 | 0.30-0.64 |
| Canonical fallback | 1/30 | 0.03 | 0.01-0.17 |
| Tool precision | 161/161 | 1.00 | 0.98-1.00 |
| Tool recall | 61/84 | 0.73 | 0.62-0.81 |

Read it as: half the raw plans were usable as returned, the repair step saved almost all the rest,
and one needed the fallback. The model never picked a tool outside the task but missed about a
quarter of the tools the gold plans use. Nothing about this let it put a wrong number in a report,
because it is never asked for one.

In CEAP:
- `src/ceap/llm/client.py`: `LLMClient`, `MockLLMClient` (the keyless default), `canonical_plan`.
- `src/ceap/llm/schemas.py`, `src/ceap/llm/prompts.py` and `src/ceap/llm/prompts/`: schemas and prompt templates.
- `src/ceap/llm/ollama.py`: the local-model client.
- `src/ceap/agents/planner.py`, `src/ceap/agents/reporter.py`, `src/ceap/agents/critic.py`: the three LLM roles.

## Offline exercise

Rescore the recorded seed-1 planner, reporter and critic replies (about 95 s on the test laptop; no
Ollama, no key):

```bash
python -m ceap.eval.local_model --replay --seeds 1
```

Find the planner block in the JSON it prints and check the seed-1 values against the per-seed
column of the source table (valid 0.50, repaired 0.40, fallback 0.10). Then open
`src/ceap/llm/client.py` and read `canonical_plan`: what does it do that the model could not?

## Pitfalls

- Treating a seed as making a model deterministic everywhere. It pins one server and one model digest.
- Asking the model for a figure "to save a tool call". The figure then has no evidence id.
- Counting a repaired plan as a model success. CEAP reports the three outcomes separately.

## Self-check

<details><summary>1. Why does CEAP ask for schema-constrained JSON rather than free text for plans?</summary>
So a plan can be validated mechanically: unknown tools, bad arguments and missing fields are
detected, repaired once, sanitised, or replaced by the canonical plan.</details>

<details><summary>2. What does a higher temperature do?</summary>
It flattens the next-token distribution, so less likely tokens are sampled more often.</details>

<details><summary>3. In seed 1, how many of 10 plans fell back to the canonical plan?</summary>
One (0.10 in the per-seed column).</details>

<details><summary>4. Where does a latency figure in a report come from?</summary>
From deterministic code in `ceap.analytics` or a tool result, never from the model.</details>

## Further reading

- [docs/ai-agents/ai-agents.md](../../ai-agents/ai-agents.md), "The one rule everything else follows".
- [LEARN.md](../../../LEARN.md), section 3.
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
