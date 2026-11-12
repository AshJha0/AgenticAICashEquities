# Exercises

Hands-on work to go with [LEARN.md](../LEARN.md). Each exercise names the section it builds on,
what to do, and how to know you got it right. Everything runs offline with the mock LLM.

## Level 1: read and run

1. **IS by hand (LEARN §5).** A buy of 1,000 shares is decided at 50.00, 800 fill at 50.10 and the
   price ends at 50.40. Compute implementation shortfall in bps with the opportunity leg, then check
   it with `implementation_shortfall_bps(Side.BUY, 50.0, [50.10], [800], 1000, final_price=50.40)`.
   Answer: execution 800 x 0.10 = 80, opportunity 200 x 0.40 = 80, total 160 over a 50,000 notional
   = 32 bps.
2. **Run one scenario (LEARN §13).** `ceap investigate "why did IS rise?" --dataset T06` and find the
   evidence id that backs each number in the report.
3. **Read a VOID result (ADR 0005).** In `docs/research/result_momentum_2026-11.md`, which gate failed
   and by how much? Why was no Sharpe ratio computed?

## Level 2: change something and watch the tests

4. **Kill a mutant (docs/testing/test-pyramid.md).** In `shortfall_components`, change
   `(m > 0)` to `(m >= 0)` and run `pytest tests/properties/test_r090_mutation_gaps.py --no-cov`.
   Which test fails, and why does a zero mid matter? Revert the change.
5. **Write a metamorphic test.** For `realised_volatility_bps`, reversing the price path should not
   change the result. Is that true? Write the test in `tests/properties/` and explain the answer.
6. **Break the policy (LEARN §11).** Read the test in `tests/adversarial/test_research_adversarial.py`
   that hands a quant a staging step. Which `_rule_*` in `src/ceap/policy/engine.py` denies it, and
   what would the task have done if the harness had skipped the step instead of failing?

## Level 3: extend

7. **A new signal ([how-to-extend.md](how-to-extend.md) §1).** Add a 20-day reversal signal with a
   unit test, then compare it with `reversal_5` on R01 using `ceap profitability`.
8. **A new read-only tool (§2).** Add the gross-exposure tool from the guide, a test in `tests/mcp/`,
   and check that the catalogue hash changed.
9. **A preregistration.** Write, before running anything, the gates and the decision rule for testing
   exercise 7's signal. Then run it once and report it under the rule you wrote, including VOID or
   INCONCLUSIVE if that is what the rule says.
