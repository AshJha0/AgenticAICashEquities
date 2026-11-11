# AI concepts in CEAP: a tutorial track

Seven short lessons for students and quants who are new to applied AI. Each one teaches one flavour
of AI that this repository actually uses, shows where it lives in the code, works one example with
numbers copied from a committed source, and ends with an exercise that runs offline: no API key, no
network, no Ollama. Exercises use the mock client, recorded cassettes and the deterministic tools.

## Learning path

| # | Lesson | What you leave with |
|---|---|---|
| 1 | [LLMs](01-llms.md) | tokens, sampling, schema-constrained output, repair and fallback; why the model never computes a number |
| 2 | [Agents and the harness](02-agents-and-the-harness.md) | plan, validate, execute tools over MCP, critic, reporter; the harness owns governance |
| 3 | [Guardrails](03-guardrails.md) | the policy engine, nonce data blocks, numeric traceability, human approval |
| 4 | [RAG](04-rag.md) | BM25 versus hybrid plus reranker, recall at k, leaks on near-miss negatives, abstention |
| 5 | [Classical ML](05-classical-ml.md) | the change-point model, calibration (Brier, ECE, reliability) |
| 6 | [Honest AI evaluation](06-evaluating-ai.md) | golden sets, Wilson intervals, small-n honesty, a kappa-gated judge, replay versus live |
| 7 | [Local models](07-local-models.md) | Ollama, pinned digests, cassettes, what a 7B CPU model did |

Read them in order; lessons 3 and 6 lean on 1 and 2.

Same concepts, another desk: the AgenticTrader repository has a matching track,
[ai_learning_path.md](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md)
(site: https://ashjha0.github.io/agentic-trader/). That link goes live once that repository's
pull request merges.

## Prerequisites

- Python and `pytest` at the level of reading a test file.
- Basic probability: a proportion, a confidence interval, a mean.
- The guided tour in [LEARN.md](../../../LEARN.md), sections 3 (the boundary) and 7 (the harness),
  is useful context but not required.

## How to run the exercises

From the repository root, with the package installed (`pip install -e ".[dev]"`, see the
[README](../../../README.md)). If you run from a checkout without installing it, prefix commands
with `PYTHONPATH=src`. Nothing here needs a key: with no `ANTHROPIC_API_KEY` set the platform uses
`MockLLMClient`, and the local-model replay reads committed cassettes.

Timings in the lessons were measured on one laptop while another test run was using the CPU; treat
them as an upper bound.

Each lesson has the same headings: Plain idea, Formal statement, Worked example from this repo
(with an "In CEAP" list of modules), Offline exercise, Pitfalls, Self-check, Further reading.
