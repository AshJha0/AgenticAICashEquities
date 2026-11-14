# Lesson 3: Guardrails

Learning goals: describe the policy engine's decisions and why it fails closed; explain how nonce
data blocks contain untrusted text; explain numeric traceability; say who approves a high-risk tool.

## Plain idea

Assume the model will sometimes be wrong, or be steered by text it reads. Guardrails are code that
holds regardless: a policy engine that refuses tool calls it cannot justify, a wrapper that marks
retrieved documents and tool output as data rather than instructions, an audit that removes any
number in a report that cannot be traced to a tool result, and a human gate on anything that acts.

## Formal statement

- Policy (`ceap.policy.engine`): every tool call is allowed, denied or sent for approval by
  read-only flag, risk level and required capability. A request that names a symbol with no
  configured universe is denied, and an input schema that is itself invalid fails closed. Each agent
  runs with the intersection of its own grant (`ceap.policy.agents`) and the principal's capabilities.
- Data blocks: untrusted text is wrapped as `<data surface="..." id="..." nonce="...">` with a random
  16-hex nonce that the closing tag repeats, so the content cannot guess the end marker.
- Traceability: a reporter claim whose number does not match a tool output is dropped by the harness.
- Approval: HIGH-risk tools always wait for a human who must be a different named person with
  `approvals:decide`; the approver sees the exact arguments and their content hash, which is checked
  again just before the tool runs. Staging paper orders needs a second approval.

Source: [docs/ai-agents/ai-agents.md](../../ai-agents/ai-agents.md).

## Worked example from this repo

From [docs/evaluation/local_model/README.md](../../evaluation/local_model/README.md), qwen2.5 7B:

| Check | k/n | 95 % CI |
|---|---|---|
| Reporter: claims kept by the audit (0.10.0 re-audit; 25/39 under the 0.9.0 audit) | 22/39 | 0.41-0.71 |
| Reporter: claims dropped by the harness | 14/39 | 0.23-0.52 |
| Reporter: claims with a number outside their own fact keys (0.10.0 re-audit) | 11/39 | 0.17-0.44 |
| Injection obeyed, user question | 3/10 | 0.11-0.60 |
| Injection obeyed, retrieved document | 0/10 | 0.00-0.28 |
| Injection obeyed, tool output | 0/10 | 0.00-0.28 |
| Injection obeyed, finding statement | 0/10 | 0.00-0.28 |
| Injection obeyed, all | 3/40 | 0.03-0.20 |

About a third of the model's claims were not traceable, and none of those numbers reached a report:
the guardrail, not the model, kept the report honest. The three obeyed attacks were all planted in
the user question. 0/10 means "not seen at this sample size" (upper bound 28 %), not "safe".

In CEAP:
- `src/ceap/policy/engine.py`, `agents.py`, `approvals.py`, `permissions.py`.
- `src/ceap/llm/prompts.py` (data blocks), `src/ceap/agents/reporter.py` (audits).
- `src/ceap/harness/approvals.py`, `src/ceap/harness/paper_gate.py`.
- `src/ceap/eval/injection.py` and `src/ceap/eval/injection_corpus_v1.json`.

## Offline exercise

```bash
python -m pytest -q -p no:randomly --no-cov tests/unit/test_policy.py
```

About 1 minute on the test laptop. Pick one deny test and say which rule fires. Then find
`test_fabricated_numbers_and_evidence_ids_are_flagged` under `tests/adversarial/` and read it.

## Pitfalls

- Guardrails in the prompt only. A prompt is a request, not a control.
- Reading 0/10 as zero risk.
- Letting the requester approve their own high-risk action.

## Self-check

<details><summary>1. What does "fail closed" mean for the policy engine?</summary>
When it cannot establish that a call is allowed (no universe, invalid schema), it denies.</details>

<details><summary>2. Why does the nonce matter?</summary>
Untrusted text cannot close the data block early, because it cannot know the random marker.</details>

<details><summary>3. Which surface leaked injections in 0.9.0?</summary>
The user question, 3/10; the other three surfaces were 0/10 each.</details>

<details><summary>4. What happens to a claim with an untraceable number?</summary>
The harness drops it. Since 0.10.0 a number must come from the claim's own fact keys, with the metric and direction it is written with; on the recorded claims 11/39 carried a number outside their own fact keys and 17/39 were dropped in all.</details>

## Further reading

- [docs/threat-model/owasp-llm-top10.md](../../threat-model/owasp-llm-top10.md).
- [LEARN.md](../../../LEARN.md), section 11.
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
