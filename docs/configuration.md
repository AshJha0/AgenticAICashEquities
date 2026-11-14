# Configuration and CI checks

Moved from the README in 0.9.0. Environment variables are read in `src/ceap/config.py` and the
modules named in each row; CI jobs are in `.github/workflows/`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CEAP_ENV` | `dev` | `dev` enables the dev API keys and auto-approval; any other value requires `CEAP_API_KEYS` and `CEAP_LLM_PROVIDER`, queues approvals, and refuses to start with `CEAP_AUTO_APPROVE=true` |
| `ANTHROPIC_API_KEY` | – | key for `CEAP_LLM_PROVIDER=anthropic` |
| `CEAP_LLM_PROVIDER` | `mock` in dev, required elsewhere | `mock` / `anthropic` (`auto` was removed in 0.4.0) |
| `CEAP_LLM_MODEL` | `claude-sonnet-5-5` | narrative / critique model; must be pinned in `llm/model_pins.toml` |
| `CEAP_LLM_PLANNING_MODEL` | = model | stronger planning model; **recommended for Stage 2** — a weak planner (tested: Haiku alone) can produce an incomplete research plan; pairing a stronger planner with a smaller narrative model took the 21-scenario evaluation from 5 incomplete plans to 0 (see CHANGELOG 0.3.1); the pinned default is `claude-opus-5-5` |
| `CEAP_LLM_DISABLED` | `false` | kill switch: no model call anywhere, deterministic plan / critique / narrative, banner on every result |
| `CEAP_LLM_TIMEOUT_S` | `60` | per model call |
| `CEAP_LLM_MAX_CONCURRENCY` / `CEAP_LLM_MAX_CALLS_PER_TASK` | `4` / `12` | model-call concurrency cap and per-task call cap (the router enforces it too) |
| `CEAP_TASK_BUDGET_USD` / `CEAP_TASK_TOKEN_BUDGET` | `2.00` / `400000` | per-task caps, on by default since 0.8.0. Each call reserves its upper-bound local estimate (estimated input + `max_tokens`) before it is sent and settles to the actual cost after, so concurrent calls cannot overshoot; `off` removes a cap |
| `CEAP_LLM_BREAKER_FALLBACKS` / `CEAP_LLM_BREAKER_COOLDOWN_S` | `5` / `60` | circuit breaker: after this many consecutive primary-model failures the primary is skipped (deterministic fallback, flagged DEGRADED) for the cooldown |
| `CEAP_RATE_LIMIT_PER_MINUTE` / `CEAP_RATE_LIMIT_BURST` | `60` / `20` | per-principal token bucket on `POST /investigations` and `POST /research`; an empty bucket answers 429 with `Retry-After`; `off` disables. In-process, so per replica |
| `CEAP_CPU_TOOL_PROCESSES` | `2` under the API server, `0` elsewhere | worker processes for CPU-bound MCP tools (`run_backtest`, `evaluate_signal`); the server starts them before it serves, and a step timeout or cancel terminates only that call's worker. `0` runs them in a thread (loop stays free, but a timed-out call runs to completion). Workers use `spawn`, so a script that sets this must keep its top level under `if __name__ == "__main__":` |
| `CEAP_PREWARM_DATASETS` | – (`T01` in the Docker image) | scenario ids generated at start-up in a worker thread, so the first request does not pay the generation cost |
| `CEAP_MCP_TRUSTED_SERVERS` | the built-in servers | comma-separated allowlist; every tool of any other MCP server is denied, whatever it declares (0.8.0; it was treated as read-only) |
| `CEAP_MCP_CATALOGUE_PINS` | – | `server=<sha256>,...`; start-up is refused when a server's tool catalogue hash differs |
| `CEAP_AUDIT_HMAC_KEY` / `CEAP_AUDIT_HMAC_OLD_KEYS` | published dev key | HMAC key for the audit log and paper ledger chains, kept outside the database; without it results report `dev_key: true`. Old keys allow rotation |
| `CEAP_ATTRIBUTION` | heuristic | `regression` turns on the regression cause attribution (F8); off, output is byte-identical to 0.7.1 |
| `CEAP_MCP_PRINCIPAL_ROLES` | – | roles a standalone stdio MCP server acts for; unset means no capabilities, so `stage_orders` is refused |
| `CEAP_DEFAULT_DATASET` | `T01` | scenario dataset when a request names none |
| `CEAP_STEP_TIMEOUT_SECONDS` | `30` | per tool call |
| `CEAP_TASK_TIMEOUT_SECONDS` | `300` | per investigation |
| `CEAP_MAX_RETRIES` | `2` | transient tool failures |
| `CEAP_AUTO_APPROVE` | `true` in dev, else `false` | `false` queues approvals for `/approvals`; `true` outside dev refuses to start. A harness built without a gateway denies every approval |
| `CEAP_API_KEYS` | dev keys (dev only) | `key:role,...` (roles: viewer, trader, quant, engineer, approver, admin; only `approver` decides approvals, never `admin`, and never one its person requested or an earlier stage it approved; HIGH-risk tools always need an approval); keys must be ≥ 8 characters, and outside dev a raw key needs at least 256 bits of estimated entropy and 16 distinct characters or the app refuses to start (0.9.0, `ceap.api.auth`; generate with `secrets.token_urlsafe(32)`); held only as salted digests in memory, never logged. `auditor` reads every principal's tasks and `/metrics` but acts on nothing; every other reader sees only its own tasks. Research needs the `research` capability (quant, admin); order staging needs `trading:execute` (admin) |
| `CEAP_MAX_CONCURRENT_INVESTIGATIONS` | `4` | background tasks run under a semaphore; a task waiting for a human approval gives its slot back |
| `CEAP_NIGHTLY` | – | `1` runs the nightly-marked sweeps (set by `nightly.yml`) |
| `CEAP_DB_PATH` | `:memory:` in dev, else `var/ceap.db` | SQLite file for tasks, results, approvals, staged orders, idempotency keys and the audit log; migrated forward on start (backup first); see `ceap db --help` |
| `CEAP_APPROVAL_TIMEOUT_S` | `120` | an unanswered approval expires (decided by `timeout`) |
| `CEAP_RETENTION_RESULTS_DAYS` / `_TRACES_DAYS` / `_LLM_IO_DAYS` | `90` / `30` / `7` | retention applied on start and by `ceap db prune` (`0` keeps forever); the audit log is never pruned |
| `CEAP_DB_MAX_BYTES` | 1 GiB | size bound: oldest results are pruned first |
| `CEAP_MAX_BODY_BYTES` | `65536` | larger request bodies get 413 |
| `CEAP_API_KEY_HASHES` | – | pre-hashed keys `sha256$salt$digest:role[:YYYY-MM-DD][:subject=NAME]` (expiry and subject optional); generate with `ceap.api.auth.hash_api_key`. A mapped key acts as `user:NAME`, so four-eyes compares persons |
| `CEAP_API_KEY_SUBJECTS` | – | `role:xxxxxxxx=NAME,...`: map existing keys to named subjects |
| `CEAP_MAX_RETAINED_RESULTS` | `200` | oldest investigation results are evicted beyond this |
| `CEAP_KNOWLEDGE_DIR` | `<data root>/reference/knowledge` | RAG corpus |
| `CEAP_DATA_ROOT` | packaged `ceap/_data`, else `data/` in a checkout | where every data file is resolved (`ceap.paths.data_root`, 0.10.0 E3). The wheel and image carry the runtime data; a missing knowledge corpus or model store raises `DataFileMissing` instead of falling back. Check an installed copy with `python -m ceap.smoke` |
| `CEAP_LOCAL_MODEL` | – | `1` allows live calls to a local Ollama model in the 0.9.0 evaluation runners (`ceap.eval.local_model`, `injection`, `judge`, `challenger`); unset, they only replay recorded cassettes. Not a `CEAP_LLM_PROVIDER` value |
| `CEAP_OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint for `ceap.llm.ollama` |
| `CEAP_DURABLE_RUNS` | – | `1` turns on the step-boundary run journal and resumable approvals (`ceap.harness.durable`, 0.9.0) |
| `CEAP_APPROVAL_SWEEP_S` | `60` | interval of the API's sweep that expires abandoned PENDING approvals (0.9.0) |


## CI checks

| Check | Where | Fails when |
|---|---|---|
| ruff, mypy, banned-word check | `ci.yml` / `lint` | lint or type error; a banned word in any tracked file |
| pytest on 3.11 and 3.12; branch coverage measured on 3.12 only, floor 85 % | `ci.yml` / `test` | a test fails, or branch coverage of `ceap` on 3.12 drops below 85 % (target 90 %) |
| fast tier on Windows | `ci.yml` / `test-windows` | a test fails on windows-latest (no coverage) |
| benchmarks | `ci.yml` / `benchmarks` | a benchmark mean on the PR head is more than 25 % slower than on the base, same runner |
| API reference | `ci.yml` / `api-reference` | pdoc cannot build the reference for `ceap.analytics` and `ceap.domain` |
| `lock-fresh` | `ci.yml` | the committed lock differs from a fresh `uv` regeneration |
| `pip-audit` | `ci.yml` / `audit` | a locked dependency has a known vulnerability |
| gitleaks, with a planted-key self-test | `ci.yml` / `secrets` | a secret is in the tree or the history |
| Docker build and `ceap --help` | `ci.yml` / `docker` | the image does not build or the CLI does not start |
| CHANGELOG touched | `ci.yml` / `changelog` | a PR does not edit `CHANGELOG.md` and is not labelled `no-changelog` |
| domain and API contracts | `ci.yml` / `contracts` | a public `ceap.domain` signature or `docs/api/openapi.json` changes without the `contracts-change` label |
| golden set | `ci.yml` / `golden-change` | `tests/golden/` is edited without the `golden-change` label, or an existing cassette or snapshot without the `snapshot-change` label |
| CodeQL, OpenSSF Scorecard | `codeql.yml`, `scorecard.yml` | static analysis or supply-chain findings |
| mutation ratchet, API fuzz | `nightly.yml` / `mutation`, `api-fuzz` | surviving mutants exceed `tests/mutation/baseline.json`; Schemathesis finds a failing GET |
| every tier, random order, 1000 hypothesis examples | `nightly.yml` | a slow or nightly test fails, or a test depends on another's state |
| release gate | `release.yml` on a `v*` tag | the latest `CI` workflow run on the tagged commit is not green, versions disagree, or the CHANGELOG section, banned-word or licence check fails |

Test markers: `slow` (runs on PR; skip locally with `-m "not slow"`) and `nightly` (nightly only;
the tests skip unless `CEAP_NIGHTLY=1`, which `nightly.yml` sets). `pytest-timeout` gives each test
120 s, `slow` 600 s and `nightly` 1800 s; `CEAP_TEST_TIMEOUT_SCALE` (default 1, tests only)
multiplies the `slow` and `nightly` ceilings on a loaded machine, and CI leaves it unset. Tests that read `scripts/`, `docs/`, committed data or
`.github/` live in `tests/repo`; `tests/unit` reads nothing outside `src` and `tests`.
