# How to extend CEAP

Three common extensions, each as the smallest change that keeps the platform's rules intact: numbers
come from deterministic code (ADR 0001), the policy engine decides what a tool may do (ADR 0002),
and tools live in the MCP server for their domain (ADR 0004).

## 1. Add a research signal

A signal is a function from a close-price panel (dates x names) to a score panel of the same shape,
NaN where it is not defined. The registry is `SIGNALS` in `src/ceap/analytics/signals.py`.

1. Write the function next to `momentum_12_1`, `reversal_5` and `low_vol_60`. Use only data up to
   each date: the leakage canary and the look-ahead tests assume it.
2. Add a `SignalSpec(name, description, lookback_days, default_horizon_days, default_rebalance_days, fn)`
   entry to `SIGNALS`.
3. Add a unit test in `tests/unit/analytics/` with a hand-computed value on a tiny panel, and a
   property or metamorphic test where one is natural (for example: scaling prices does not change a
   return-based signal).
4. Try it: `ceap research "evaluate my_signal" --signal my_signal --dataset R01`.

The CLI's `--signal` choices and the research tools read `SIGNALS`, so no other registration is needed.
`SEARCH_SIGNALS` in `src/ceap/domain/research.py` is a separate, deliberately fixed list (the
trial-accounting search space); adding to it changes the multiple-testing correction, so say so in
the PR and label it `contracts-change`.

## 2. Add an MCP tool

Tools are async functions registered on the server for their domain with `@server.tool(...)`
(`src/ceap/mcp/server.py`). Example, in `src/ceap/mcp/risk/server.py`:

```python
@server.tool("Return the start-of-day gross exposure of the book at average cost, in USD.")
async def get_gross_exposure(dataset: str | None = None) -> dict[str, Any]:
    ds = select_dataset(store, dataset)
    return {"gross_usd": float(sum(abs(p.quantity * p.average_price) for p in ds.positions))}
```

- The defaults are `read_only=True`, `risk_level=RiskLevel.LOW`; a read-only tool needs the caller's
  `tools:read` capability, a mutating one `tools:write` (`_rule_read_only`). A tool with side effects must say
  `read_only=False` and a `risk_level`; the policy engine's read-only and risk rules then apply, and a
  HIGH-risk call waits for a human approval.
- The input schema is generated from the signature, and the policy engine validates arguments against
  it (`_rule_input_schema`). Keep arguments simple and typed.
- The tool catalogue is hashed (`catalogue_hash`), so CI shows the change. Add a test in `tests/mcp/`.

## 3. Add a policy rule

Rules are methods `_rule_*` on the engine in `src/ceap/policy/engine.py`, listed in order in
`self._rules`. Each takes `(request, metadata, context)` and returns an outcome or `None` to pass.
A DENY from any rule wins, then REQUIRE_APPROVAL, otherwise ALLOW, and the rule that fired is
recorded in the audit trail.

1. Write the method and add it to `self._rules` at the point where it should run.
2. Add a test in `tests/unit/test_policy.py` for the allowed case and the denied case, and an
   adversarial test if the rule exists to stop an attack.

## Before you open a PR

`pre-commit run --all-files` runs ruff, mypy, the banned-word check, the docs check and the OpenAPI
check (`.pre-commit-config.yaml`). Add an entry under "Unreleased" in `CHANGELOG.md`.
