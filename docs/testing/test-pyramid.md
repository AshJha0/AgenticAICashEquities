# How CEAP is tested

The plain idea: many fast tests that pin small pieces exactly, fewer slower tests that run whole
workflows, and a handful of checks that test the tests. Each layer catches a kind of bug the others
miss. This page says what each layer is for, where it lives and when it runs.

## The layers

Counts are test functions before parametrisation (`grep "def test_"` per folder at the 0.9.0 lane-6
commit); pytest's collected count is higher because of `@pytest.mark.parametrize` and Hypothesis.

| Layer | Folder | Functions | What it catches | Runs |
|---|---|---|---|---|
| Unit | `tests/unit/` | 478 | a formula or a rule is wrong (IS legs, policy decisions, parsers) | every PR, Linux 3.11 and 3.12, Windows fast tier |
| Property (Hypothesis) | `tests/properties/` | 19 + 0.9.0 additions | an invariant fails on an input nobody thought of | every PR (100 examples, derandomised), nightly (1000, randomised: new examples each night) |
| Metamorphic | `tests/properties/test_r090_metamorphic.py` | 0.9.0 | the answer moves the wrong way when the input moves in a known way | every PR |
| Contract | `tests/contract/` | 22 | the wire format to the LLM provider or the public `ceap.domain` signatures drifted | every PR (`lint` job) |
| MCP | `tests/mcp/` | 20 | a tool's schema, discovery or stdio transport broke | every PR |
| Agent | `tests/agent/` | 173 | the harness, planner, critic or audits misbehave with a scripted model | every PR |
| Adversarial | `tests/adversarial/` | 16 | injected instructions, fabricated numbers, rogue plans get through | every PR |
| Integration | `tests/integration/` | 62 | the API, CLI, persistence and paper job fail end to end | every PR |
| Evaluation | `tests/evaluation/` | 28 | the platform stops finding the planted root cause in the 50 + 30 scenarios | every PR; 200 to 500-seed sweeps in the nightly `sweeps` job |
| Golden | `tests/golden/` | 12 | an answer to an owner-signed question changed | every PR; edits need the `golden-change` label |
| Repository | `tests/repo/` | 17 | docs, lock, workflows or banned words drifted | every PR |
| CLI | `tests/unit/cli/` | 0.9.0 | a subcommand, exit code or usage message changed | every PR |
| Mutation | mutmut, `scripts/mutation_ratchet.py` | n/a | a test suite that runs the code but would not notice it changing | nightly; survivors may not rise |
| Benchmarks | `benchmarks/` | 4 | a hot path got more than 25 % slower | every PR (base vs head, same runner) |
| API fuzz | Schemathesis, nightly `api-fuzz` | n/a | a 5xx or a response that breaks its own OpenAPI schema | nightly |

## Three techniques worth knowing

**Property tests.** Instead of one input and one expected output, state a rule that must hold for
all inputs and let Hypothesis search for a counterexample. Formal statement: for all `x` in the
strategy's domain, `P(f(x))`. Example: for any fills, VWAP lies between the lowest and the highest
fill price (`test_vwap_lies_within_low_high`).

**Metamorphic tests.** When the right answer for a random input is unknown, test how it must change.
Formal statement: for a transformation `T` with a known effect `g`, `f(T(x)) = g(f(x))`. Example:
implementation shortfall in bps is unchanged if every price is multiplied by `k`, and negated if the
side flips (`test_is_bps_scale_invariance_and_side_flip`).

**Mutation tests.** A test suite can execute a line without checking it. mutmut changes one operator
at a time (`>` to `>=`, `+` to `-`) and reruns the tests; a change the tests do not notice
"survives". Example from 0.8.0: `m > 0` in the Perold fill-mid filter could become `m >= 0` and every
test still passed, because no test used a zero mid. `test_non_positive_or_missing_mid_equals_arrival_mid`
(0.9.0) kills that mutant. The nightly job keeps a ceiling on survivors in
`tests/mutation/baseline.json`. Until 0.10.0 that ceiling was never measured: the mutation job
crashed before testing a mutant and the ratchet passed on the empty result (R-25). The ratchet
now fails on an empty run, and on a null ceiling after `null_allowed_until`.

## Running them locally

```bash
pytest -m "not slow and not nightly" --no-cov      # the fast tier (what Windows CI runs)
pytest tests/properties --no-cov                    # properties and metamorphic only
HYPOTHESIS_PROFILE=nightly pytest tests/properties  # 1000 examples each
```

**A reliable coverage number on a loaded machine.** A pytest-timeout expiry ends the whole xdist
worker on Windows, and that worker's coverage data goes with it ("node down"), so a parallel run
under load can report a coverage figure that is too low. In 0.9.0 seven 30-75 s tests moved to the
`slow` tier (600 s ceiling) for this reason. To measure locally:

```bash
pytest -m "not slow and not nightly" -n 3 --cov=src/ceap --cov-report=     # fast tier, parallel
pytest -m "slow and not nightly" -p no:xdist --cov=src/ceap --cov-append     # slow tier, serial
```

`CEAP_TEST_TIMEOUT_SCALE` (default 1) multiplies the `slow` and `nightly` ceilings, e.g. `2` on a
busy laptop; CI leaves it unset. It does not change the harness's own per-step tool timeout
(`CEAP_STEP_TIMEOUT_SECONDS`, default 30 s): under heavy load a research backtest can exceed it,
the step degrades as designed, and a test that expects the full result (for example
`test_agents_flag_what_the_scenarios_embed`) fails for that reason alone. On a loaded machine set
`CEAP_STEP_TIMEOUT_SECONDS=90` as well; on an idle machine and in CI the default holds. To measure one module, still pass `--cov=src/ceap` and read that
module's row: `--cov=ceap.<module>` imports the package early and trips numpy's "cannot load more
than once" error.

Mutation testing needs Linux (mutmut does not run natively on Windows): `pip install mutmut==3.8.1`,
`mutmut run`, then `python scripts/mutation_ratchet.py` (WSL works). A release needs the last
three nightly runs on main green (`scripts/release_check.py`).
