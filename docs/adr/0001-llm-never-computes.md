# ADR 0001: The LLM never computes a number and never invents an evidence id

- Status: Accepted (0.1.0). Recorded 0.9.0.

## Context

CEAP answers questions such as "why did implementation shortfall rise on this AAPL order?". The
answer contains numbers (bps, fill rates, Sharpe ratios). A language model asked to produce those
numbers will sometimes produce plausible wrong ones, and nothing in the text tells the reader which.
In a trading context a wrong number reads exactly like a right one.

## Decision

Every number in a report or research proposal is computed by deterministic Python
(`ceap.analytics`, `ceap.execution`, `ceap.ml`) or read from a tool result. The model plans which
tools to call and writes narrative around structured facts it is handed. It never sets a
confidence either: confidence comes from the evidence model.

Formally: every numeric token in model output must be traceable to a fact, carrying an evidence id
produced by a tool call in the same task, that renders to that token; otherwise the output is flagged.

Worked example: the TCA tool returns `implementation_shortfall_bps = 12.4` with evidence `EV-3`. A
narrative saying "shortfall rose to 12.4 bps [EV-3]" passes; "shortfall rose to 14 bps" is flagged,
because no fact renders to 14.

## Consequences

- The platform runs end to end with `MockLLMClient` and no API key; a weak model shows up as a
  worse plan or narrative, never as a wrong metric.
- The number audit is code that has to keep up with how models phrase numbers (units, rounding,
  direction words). Plan item A1 (0.9.0) moves it to typed placeholders for this reason.
- Some fluent answers are rejected because a number cannot be traced. That is the intended trade.

## Evidence

- Rule stated in `src/ceap/__init__.py` and `docs/ai-agents/ai-agents.md` ("The one rule").
- `tests/adversarial/` feeds the reporter fabricated numbers and evidence ids and asserts the audit
  flags all of them.
