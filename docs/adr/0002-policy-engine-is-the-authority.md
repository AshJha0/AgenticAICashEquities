# ADR 0002: The policy engine is the authority for every tool call

- Status: Accepted (0.1.0). Recorded 0.9.0.

## Context

An agent's plan comes from a model, and a model can be steered by its input (prompt injection) or
simply be wrong. If the permission to stage orders or read restricted knowledge lived in the prompt
or in the plan, the model could grant it to itself.

## Decision

Every tool call goes through the harness, which asks the policy engine (the interface
`ceap.domain.policy.PolicyEngine`, implemented in `ceap.policy.engine`) to evaluate the
`ToolRequest` against the tool's `ToolMetadata` and the caller's `PolicyContext` (role, task,
environment). The engine answers allow, deny or require approval. The harness enforces the answer;
nothing upstream can override it. A denied step fails the task closed.

Worked example: a quant's research plan contains a step `execution.stage_orders`. The engine denies
it for the quant role, the harness records the denial as evidence, and the task ends failed rather
than continuing without the step.

## Consequences

- Prompts carry no authority, so a successful injection can at worst produce a plan that the engine
  then denies.
- Policy rules are data plus code and must be reviewed like code; a wrong rule is a real hole.
- Approval-required actions (order staging) need a human approver identity, which the API carries
  in a header and the audit log records.

## Evidence

- `src/ceap/harness/engine.py` (dispatch path), `src/ceap/policy/`.
- `tests/adversarial/test_research_adversarial.py` (hand-built staging step for a quant: denied,
  task fails closed); `tests/unit/test_policy.py`.
