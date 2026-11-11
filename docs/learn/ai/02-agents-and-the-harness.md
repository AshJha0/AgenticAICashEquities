# Lesson 2: Agents and the harness

Learning goals: trace one investigation through plan, validate, execute, critic and report; explain
why governance belongs to the harness and not to the model's plan; say when CEAP re-plans.

## Plain idea

An "agent" here is a component with a job (market, engineering, risk, research and so on) that calls
tools and returns findings with evidence. The model proposes a plan: which agents and tools to use.
The harness is the control plane that decides what actually happens. It validates the plan, runs
tool calls through MCP under policy, sends findings to the critic, and only then lets the reporter
write. If the model's plan tries to skip a check, the check runs anyway.

## Formal statement

`ceap.harness.engine.AgentHarness` runs an explicit state machine. Whatever the plan says, the
harness strips planner-supplied critic, validation and approval steps and appends its own tail:

- Investigation: critic, then evidence validation, then finalise.
- Research: critic, evidence validation, `HUMAN_APPROVAL` of the proposal, and only if staging was
  requested and the principal holds `trading:execute`, a second `HUMAN_APPROVAL` before
  `execution.stage_orders` (paper orders only).

A tool or required-agent failure triggers exactly one re-plan with the failure reason.
Source: [docs/ai-agents/ai-agents.md](../../ai-agents/ai-agents.md), "The harness owns governance".

## Worked example from this repo

The critic is deterministic first, model second. In 0.9.0 the statement checks in
`ceap.agents.critic_checks` took planted-fault detection from 6/15 to 15/15 on both the existing set
and a held-out set committed before the checks were written, with 0/10 false alarms on clean cases.
The source warns that the same author wrote both, so 15/15 is a regression bar, not evidence of
generalisation. Source: [docs/ai-agents/rag-hybrid-0.9.0.md](../../ai-agents/rag-hybrid-0.9.0.md),
section (b).

With a real 7B model as the LLM critic, planted faults detected were 35/45 (0.78, 95 % CI 0.64-0.87);
source: [docs/evaluation/local_model/README.md](../../evaluation/local_model/README.md).

In CEAP:
- `src/ceap/harness/engine.py`, `state_machine.py`, `planning.py`, `executor.py`, `dispatch.py`.
- `src/ceap/agents/`: `planner.py`, `critic.py`, `critic_checks.py`, `reporter.py` and the specialists.
- `src/ceap/mcp/`: the tool interface.

## Offline exercise

Run the re-plan tests and the critic statement-check tests:

```bash
python -m pytest -q -p no:randomly --no-cov tests/unit/test_r090_replan_depends_on.py
python -m pytest -q -p no:randomly --no-cov tests/unit/test_critic_checks_090.py
```

Then read one test in each file and write down what triggers the re-plan and what the critic
check flags.

## Pitfalls

- Putting governance steps in the prompt and trusting the model to keep them.
- Re-planning without a bound: CEAP re-plans once, then fails visibly.
- Treating a model critic as a check: the deterministic checks run first and cap confidence.

## Self-check

<details><summary>1. A plan omits the critic step. What happens?</summary>
The harness appends its own critic and validation tail anyway.</details>

<details><summary>2. How many re-plans follow a tool failure?</summary>
Exactly one, carrying the failure reason.</details>

<details><summary>3. Why is 15/15 on the held-out critic set not proof the checks generalise?</summary>
The same author wrote the set and the checks, knowing the five fault kinds.</details>

## Further reading

- [LEARN.md](../../../LEARN.md), sections 7 to 10.
- [docs/architecture/overview.md](../../architecture/overview.md).
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
