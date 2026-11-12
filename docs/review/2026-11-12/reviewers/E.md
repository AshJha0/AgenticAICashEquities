# Reviewer E: engineering (architecture, tests, CI, security, supply chain, packaging)

Review of tag v0.9.0 (`dd6bf3c`), 2026-11-12. Read-only; the only file written is this one.

## 1. Scope covered and what I did not get to

Covered (read or ran):

- `pyproject.toml`, `Dockerfile`, `.dockerignore`, `.gitattributes`, `deploy/compose/docker-compose.yml`,
  all of `.github/workflows/ci.yml`, `nightly.yml`, `release.yml`; permissions and pinning of the other four.
- `src/ceap/api/` (`app.py`, `auth.py`, `middleware.py`, `ratelimit.py`, most of `routes.py`),
  `config.py`, `util/redact.py`, `observability/logging.py`, `observability/tracing.py`,
  `persistence/db.py` (threading and transaction core), `persistence/repositories.py` (idempotency),
  `mcp/cpu_pool.py`, `platform.py` (constructor), `scripts/release_check.py`, `scripts/mutation_ratchet.py`.
- An AST import graph of all of `src/ceap` (package-level dependencies, mutual pairs, function-local imports).
- Static counts over `src/ceap`; `ruff check .` with the repo config (clean) and with extra rule
  families (S, C901, PLR0915, T20, DTZ, ASYNC) for information.
- `pytest --collect-only`: **1335 tests collected in 124 s** (claim: 1326; the difference of 9 was not
  chased, probably skips). 134 test files, 1179 `def test_` functions.
- Read-only GitHub queries (`gh run list`, `gh run view`, `gh api .../branches/main/protection`,
  `gh pr list`) to check what CI actually ran and which checks are required.
- Three bugs confirmed with `python -c` on pure functions (hash-only start-up, key echoed in an error,
  redaction of quantities).

Not covered, or only skimmed:

- `mypy src`: **not run to completion** (a cold run was stopped at 170 s on this machine). CI runs it.
- `pip-audit`: not run (needs the network vulnerability service; CI job `audit` runs it).
- The full suite: not run, by the rules. No coverage number was re-measured; 93 % is taken from the
  CHANGELOG, and the last nightly log I could read shows 90.67 % on older code.
- `llm/client.py`, `agents/reporter.py`, `data/synthetic.py` bodies: size and complexity only.
- `persistence/migrations/*.sql`, `chainkey.py`, `anchor.py`, `killswitch.py`: not read.
- `scripts/check_docs.py`, `check_contracts.py`, `check_banned_words.py`, `bootstrap.*`: not read.
- `docs/threat-model/`: sampled, not audited line by line. `codeql.yml`, `scorecard.yml`,
  `paper-*.yml`: permissions and pins only.
- The Docker image was not built. E-03 rests on path arithmetic and reading the code, not on a container run.

## 2. Score: 6 / 10 against a top-firm bar

- The CI skeleton is better than most internal repos I would expect to see: hashed lock installed with
  `--require-hashes` everywhere, SHA-pinned actions, least-privilege permissions, a lock-freshness job,
  gitleaks with a self-test, a release gate tied to CI, SBOM, provenance and cosign.
- But several headline engineering claims are configuration that has never executed: the mutation
  ratchet has no baseline, the mutation and API-fuzz jobs have never run, and the nightly was not green
  on any of the three runs before the tag.
- The shipped deployment path is not tested beyond `ceap --help`: the compose example cannot start,
  and the image and wheel do not contain the reference data, models and knowledge base that the code
  looks for, so they degrade silently.
- Layering is not enforced and has drifted: 15 mutual package-level dependencies, 228 function-local
  imports used largely to break cycles.
- Type checking is strict for three packages only; the policy engine, harness, LLM layer and
  persistence are checked without `disallow_untyped_defs`.
- Small but real bugs in security-adjacent code (idempotency race, key echoed in an error, blocking
  SQLite calls on the event loop).

## 3. What is good (verified)

- No coverage inflation by configuration: there is no `[tool.coverage]` section, no `.coveragerc`, no
  omit list; the only exclusions are 25 `pragma: no cover` lines, of which 9 are `TYPE_CHECKING` /
  `__main__` guards and 11 are the optional `fastembed` path in `src/ceap/rag/dense.py:89-135`. The CI
  floor is real: `.github/workflows/ci.yml:74` (`--cov-branch --cov-fail-under=90`).
- `--require-hashes` is used in every CI install (`ci.yml:35`, `:64`, `:96`), in nightly, release
  (`release.yml:33`) and the image (`Dockerfile:18`). `lock-fresh` regenerates the lock and diffs it
  (`ci.yml:169-182`).
- Every action is pinned to a commit SHA; the workflow default is `contents: read` (`ci.yml:9-10`);
  write scopes are per job (`release.yml:47-50`).
- API key comparison is constant-time and scans every record (`src/ceap/api/auth.py:84-86`, `:202-208`);
  cross-principal task probing returns 404, not 403 (`src/ceap/api/routes.py:312-315`).
- Fail-closed configuration outside dev: no keys, dev keys or auto-approve refuse to start
  (`src/ceap/config.py:181-192`); the image defaults to `CEAP_ENV=prod` and a non-root user
  (`Dockerfile:39-44`), base image pinned by digest (`Dockerfile:8`), healthcheck present (`:46`).
- No `pickle`, `yaml.load`, `eval`, `shell=True` or `os.system` in `src/ceap` or `scripts` (grep). One
  `subprocess.run` in library code (`src/ceap/studies/industry_momentum.py:344`, a git call). SQL is
  parameterised; the two f-string statements interpolate placeholders or an integer
  (`src/ceap/persistence/repositories.py:77`, `src/ceap/persistence/db.py:182`).
- Model files are JSON and the loader recomputes the content hash and refuses a mismatch
  (`src/ceap/ml/registry.py:237-240`).
- Zero `TODO/FIXME/XXX`, zero bare `except:`, zero `cast(` in `src/ceap`. 24 `type: ignore`, all with
  an error code, 9 of them in dynamic pydantic model building (`src/ceap/llm/schemas.py:135-177`).
- The `test_cov_090_*` files are not coverage padding. In the 13 files (155 tests, about 500
  asserts) the ones I read assert worked numbers, for example a known generator recovered to
  tolerance (`tests/unit/test_cov_090_eval_reports.py:46-59`).
- `.gitattributes` forces LF with CRLF for `.cmd/.ps1`; no `open()` / `read_text()` without
  `encoding=` was found in `src/ceap` or `scripts`.
- `build/` and `dist/` are ignored (`.gitignore:6-7`) and not present in this worktree.

## 4. Findings

No P0. Four P1, eleven P2, nine P3.

### E-01 [P1] The nightly was not green at release; the mutation and API-fuzz jobs have never run; no mutation score exists
- Evidence: `tests/mutation/baseline.json:3` (`"survived": null`); `scripts/mutation_ratchet.py:47-49`
  (null baseline: report and pass); `.github/workflows/nightly.yml:41` and `:65` (the two jobs);
  `scripts/release_check.py:44` (`GATING_WORKFLOWS = {"CI"}`); `README.md:200` ("nightly mutation
  testing with a ratchet"); `docs/testing/test-pyramid.md:26` ("survivors may not rise").
  Run history (`gh run list --workflow nightly.yml`): 2026-11-09 success; 11-10 **failure**
  (run 42805061317: `test_broker_gap_power_100_seeds` and `test_cause_recall_200_seeds` hit the 1800 s
  timeout, `test_3a4_invariants_hold_under_every_fake_client` raised a Hypothesis `FlakyFailure`);
  11-11 **cancelled** (both legs at the 60-minute job limit); 11-12 3.12 success, 3.11 **cancelled**
  at 60 minutes. Every one of those runs contains only the `full` jobs: the schedule fired before the
  0.9.0 merge, so `mutation` and `api-fuzz` have zero executions.
- Status: VERIFIED (read the files; queried the run history and the failed log)
- Confidence: high
- Why it matters at a top firm: a reader who opens the Actions tab sees a red nightly next to a
  release that lists mutation testing and fuzzing as delivered. The CHANGELOG does say the ceiling is
  `null` until the first nightly (`CHANGELOG.md:340-341`), so this is not concealed, but the README and
  the test-pyramid page state it as working, and nobody can answer "what is the mutation score". The
  nightly run time went from 11 minutes to over 45 between 11-09 and 11-10, which is a regression
  nobody triaged, and the tier that exists to catch hangs and order dependence is the one that is red.
  A release can be cut with the nightly red by design.
- Fix: (1) trigger the nightly by `workflow_dispatch` on the tag, get all four jobs green, commit the
  measured survivor count and the killed/total score to `baseline.json`; (2) make
  `mutation_ratchet.py` fail on a null baseline and when `mutmut results` reports zero mutants (today
  `mutmut run || true` plus an empty result passes); (3) open an issue automatically when the nightly
  fails, as `paper-daily.yml` already does; (4) either add "latest nightly on main is green" to
  `release_check.py` or state in the README that the nightly does not gate. Acceptance: a nightly run
  on the tagged commit with `full`, `mutation` and `api-fuzz` all green, and a unit test that the
  ratchet exits 1 for `{"survived": null}`.

### E-02 [P1] Hash-only API-key configuration is refused at start-up, so the shipped compose example cannot start
- Evidence: `src/ceap/config.py:181` (`if not self.api_keys: raise SettingsError(...)`);
  `src/ceap/api/auth.py:10-12` ("so a deployment need not hold raw keys at all");
  `deploy/compose/docker-compose.yml:17-18` (only `CEAP_API_KEY_HASHES` is set, with `CEAP_ENV: prod`);
  `docs/RUNBOOK.md:134` ("Prefer pre-hashed keys").
- Status: VERIFIED. Ran `Settings().validate_for_serving()` with `CEAP_ENV=prod`,
  `CEAP_LLM_PROVIDER=mock`, `CEAP_AUTO_APPROVE=false` and a valid `CEAP_API_KEY_HASHES` entry: it raises
  "no API keys configured for CEAP_ENV='prod'". The only test that uses hashes also passes a raw key
  (`tests/integration/test_r4_platform.py:423`).
- Confidence: high
- Why it matters at a top firm: the recommended secure mode does not work, and the deployment
  example in the repository fails on `docker compose up`. A senior platform engineer will try exactly
  that first.
- Fix: `validate_for_serving` should refuse only when both `api_keys` and the parsed `api_key_hashes`
  are empty, and should parse the hashes there so a malformed entry fails at start-up rather than on
  the first request. Acceptance: an integration test that starts the app with hashes only and gets 200
  with the matching key, plus a CI step that runs the compose file and curls `/health`.

### E-03 [P1] The wheel and the Docker image do not contain the data the code resolves from the repository root; the product degrades silently
- Evidence: about 25 module constants of the form `Path(__file__).resolve().parents[3] / "data" / ...`,
  for example `src/ceap/rag/retrieval.py:47` (knowledge base), `src/ceap/ml/registry.py:38` (model
  registry), `src/ceap/analytics/backtest.py:62` (risk-free series),
  `src/ceap/analytics/research_assessment.py:53`, `src/ceap/paper/config.py:17`.
  `pyproject.toml:64` packages only files under `src/ceap`. `Dockerfile:22` installs the project
  non-editable into `/opt/ceap`, and `Dockerfile:36-37` copies `data` to `/app/data`. From
  `/opt/ceap/lib/python3.12/site-packages/ceap/rag/retrieval.py`, `parents[3]` is
  `/opt/ceap/lib/python3.12`, not `/app`. The fallbacks are silent:
  `src/ceap/rag/retrieval.py:223-225` (empty knowledge base, one warning),
  `src/ceap/ml/anomaly.py:454-458` (unfitted default detector),
  `src/ceap/analytics/calibration.py:171-174` (no calibration map). The only image test is
  `docker run --rm ceap:ci ceap --help` (`.github/workflows/ci.yml:231`).
- Status: VERIFIED by reading and path arithmetic; SUSPECTED for the exact runtime behaviour, because
  I did not build the image.
- Confidence: medium-high
- Why it matters at a top firm: every committed evaluation number was measured from a source
  checkout. The artefacts that are signed, attested and given an SBOM (wheel, and the image built in
  the release job) run a different configuration: no policy corpus for citations, default anomaly
  detector, uncalibrated confidences. `src/ceap/data/calendar.py:153` shows the authors know the wheel
  lacks the data, but only that one module handles it deliberately.
- Fix: move runtime reference data under the package (`src/ceap/_data/...`) and load it with
  `importlib.resources`, or introduce one `CEAP_DATA_DIR` setting with a single resolver and set it in
  the Dockerfile; fail loudly outside dev when the knowledge base or a registered model is missing.
  Acceptance: a CI job that builds the wheel, installs it into a clean venv outside the repository,
  runs one investigation with the mock model and asserts a non-empty knowledge base and the registered
  model hashes in the result; the same assertion against the container.

### E-04 [P1] Branch protection does not require the checks that guard the evidence; no review, admins exempt
- Evidence: `gh api repos/.../branches/main/protection` returns required contexts `lint`,
  `test (3.11)`, `test (3.12)`, `lock-fresh`, `audit`, `secrets`, `docker`, `changelog`, `contracts`;
  `strict: false`, `enforce_admins: false`, required approving reviews `0`. Not required:
  `test-windows` (`.github/workflows/ci.yml:78`), `benchmarks` (`:103`), `golden-change` (`:288`,
  which also carries the snapshot and cassette label gate), CodeQL.
- Status: VERIFIED (API query on 2026-11-12)
- Confidence: high
- Why it matters at a top firm: the label gate on the owner-signed golden set and on recorded
  cassettes is presented as a control, but a pull request can merge with that job red. The same holds
  for the Windows tier and the benchmark gate. With zero reviews and admins exempt, the separation of
  duties that the product enforces on its own users (four-eyes) does not exist for its own evidence.
  For a one-person repository that is understandable; it should be stated, not implied otherwise.
- Fix: add `golden-change`, `test-windows` and the CodeQL analysis to the required contexts (the
  conditional jobs report "skipped" as success on push, so this is safe), set `enforce_admins`, and
  add a `tests/repo` check that compares the required contexts with the job list in `ci.yml`.
  Acceptance: the API query lists them and a pull request that edits `golden_set.json` without the
  label cannot be merged.

### E-05 [P2] Package layering is not enforced and has decayed: 15 mutual dependencies, 228 function-local imports
- Evidence: AST scan of `src/ceap`: 682 top-level and 228 function-local `ceap.*` imports. Mutual
  package pairs include `analytics <-> data`, `analytics <-> ml`, `harness <-> persistence`,
  `harness <-> platform`, `persistence <-> platform`, `persistence <-> paper`, `agents <-> harness`,
  `eval <-> harness`, `eval <-> rag`, `config <-> llm`, `llm <-> observability`, `mcp <-> paper`.
  Specific violations: `src/ceap/analytics/tca_aggregate.py:43` (deterministic analytics imports the
  approval gateway from `policy`); `src/ceap/policy/agents.py:42` (policy imports `agents`);
  `src/ceap/harness/common.py:280` and `src/ceap/rag/hybrid_eval.py:26` (production code imports
  `ceap.eval`); `src/ceap/persistence/cli.py:123-128` (persistence imports harness, llm and platform);
  `src/ceap/config.py:20` with `src/ceap/llm/router.py:32` (config and llm import each other);
  `src/ceap/domain/tasks.py:11` (domain imports `util.redact`).
- Status: VERIFIED (scan) for the facts; JUDGEMENT for the severity
- Confidence: high
- Why it matters at a top firm: ADR 0001 and 0002 rest on boundaries (the model never computes, the
  policy engine is the authority). Those boundaries are conventions today. When `analytics` can import
  `policy` and `ml`, and production code imports the evaluation package, nothing stops the next change
  from routing a number through the wrong layer. The lazy imports also hide import errors until a code
  path runs. Three near-identical names (`eval/`, `evaluation.py`, `evaluation_report.py`) add to it.
- Fix: write the intended layers down (`domain` < `util` < `analytics`/`data` < `ml` < `policy`/`rag`/
  `llm` < `mcp`/`agents` < `harness` < `platform` < `api`/`cli`; `eval` on top) as an import-linter
  contract in CI, with the current violations listed as explicit, shrinking exceptions. Move
  `eval.stats.rate` to `util`, the challenger view out of `eval`, the CLI modules out of `persistence`
  and `paper`. Acceptance: `lint-imports` green in the `lint` job and the exception list shorter each
  release.

### E-06 [P2] Blocking SQLite calls run on the event loop; `Database.run` has no timeout
- Evidence: `src/ceap/persistence/db.py:4-5` ("so the event loop never blocks on disk I/O");
  `src/ceap/persistence/db.py:203-209` (`run` waits on `Future.result()` with no timeout). `arun` has
  exactly one caller (`src/ceap/platform.py:339`). Calls from coroutines:
  `src/ceap/api/routes.py:112`, `:277`, `:330`, `:395`, `:552`; `src/ceap/platform.py:168` (every audit
  append, used by the approval gateway); `src/ceap/api/loopmon.py:74`.
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: with `synchronous=FULL` each audit append is an fsync, taken on the
  event loop thread. A slow or stuck disk stalls every request, and because `run` has no timeout the
  loop can block indefinitely; the `arun` timeout added for G7 protects one statement. The module
  docstring claims the opposite of what the callers do.
- Fix: make the repository methods used by the API and the harness async (`arun`), or wrap them in
  `asyncio.to_thread`; give `run` a timeout. Acceptance: the existing event-loop-lag test extended
  with an injected 2 s delay on the writer thread during `POST /investigations` and
  `POST /approvals/{id}`, asserting loop lag stays under 100 ms.

### E-07 [P2] `Idempotency-Key` does not hold under concurrent retries
- Evidence: `src/ceap/api/routes.py:112` (lookup), `:250` (`await asyncio.to_thread(p.validate_request, req)`
  between lookup and store), `:277` (store); `src/ceap/persistence/repositories.py:287`
  (`INSERT OR IGNORE`, the losing insert is dropped without a signal). Same shape in `POST /research`
  (`routes.py:500-552`).
- Status: VERIFIED by reading (not reproduced with a test)
- Confidence: high
- Why it matters at a top firm: the header exists for the client that times out and retries while
  the first request is still in flight. Two such requests both miss the lookup, both start a task,
  and the second caller receives a task id that the stored key does not map to. A cold dataset makes
  the window seconds wide. For anything that stages orders this is the classic duplicate-submission bug.
- Fix: reserve the key first (insert a pending row in the same transaction as the lookup; the loser
  waits for or returns the winner's response, or gets 409), then fill in the response. Acceptance: a
  test that fires two identical POSTs with one key through `asyncio.gather` with a slowed
  `validate_request` and asserts one task and identical responses.

### E-08 [P2] A malformed `CEAP_API_KEYS` entry echoes the raw key in the error
- Evidence: `src/ceap/config.py:220` (`f"CEAP_API_KEYS entry {pair!r} must be key:role"`), against
  `src/ceap/api/auth.py:14` ("The raw key is never logged, echoed in an error, or kept in a repr").
- Status: VERIFIED (ran `_parse_api_keys("SuperSecretKeyValueWithoutRole123")`: the message contains the key)
- Confidence: high
- Why it matters at a top firm: the commonest configuration mistake (forgetting `:role`) puts a
  production key into the start-up traceback and from there into the log pipeline. It is a small bug
  that falsifies a written security statement.
- Fix: report the entry's position, not its content. Acceptance: a parametrised test that no
  `SettingsError` raised by `config.py` or `auth.py` contains any substring of the supplied key.

### E-09 [P2] The redactor treats 8 to 19 digit numbers as account numbers, which removes share quantities, notionals, order ids and epoch times
- Evidence: `src/ceap/util/redact.py:91` (`ACCOUNT`: `\d{8,19}`); applied to the stored task
  description (`src/ceap/domain/tasks.py:77`) and to every log line and traceback
  (`src/ceap/observability/logging.py:33`, `:39`).
- Status: VERIFIED (ran `redact("Why did the 12500000 share VOD.L order slip?")`: returns
  "Why did the [REDACTED:ACCOUNT] share VOD.L order slip?"; `"notional 250000000 GBP"` and
  `"order id 20261112000123"` are redacted the same way)
- Confidence: high for the behaviour; medium for downstream impact (I did not trace whether the
  planner prompt uses the redacted description or the original question)
- Why it matters at a top firm: in an execution-quality tool the numbers that matter in a log are
  quantities, notionals and order ids. A redactor tuned for consumer PII makes incident logs
  unreadable and may alter the user's question before it is stored. The module comment argues a false
  positive is cheap; in this domain it is not.
- Fix: drop the bare-digit `ACCOUNT` rule or require context (a preceding "account", "acct", "sort
  code", or card grouping with a Luhn check); never redact the structured fields of a task.
  Acceptance: property test that integers and decimals in a question, and ids in the house formats,
  survive `redact` unchanged, while the current positive cases still redact.

### E-10 [P2] OpenTelemetry export is dead code
- Evidence: `src/ceap/observability/tracing.py:155` (`build_tracer`) has no caller in `src/ceap`; the
  harness and platform construct `InMemoryTracer()` directly (`src/ceap/harness/engine.py:122`,
  `src/ceap/platform.py:238`). `OpenTelemetryTracer` (`tracing.py:136`) is excluded from coverage and
  never configures a `TracerProvider` or exporter (the only `TracerProvider` in the repository is in
  `tests/integration/test_r4_platform.py:519`). No OTLP exporter package is in `requirements.lock`.
  `pyproject.toml:36-40` still ships an `observability` extra with the OTel API and SDK.
- Status: VERIFIED (grep)
- Confidence: high
- Why it matters at a top firm: setting `OTEL_EXPORTER_OTLP_ENDPOINT` does nothing, and the extra
  adds dependencies (and CVE surface) the product never uses. A reader who checks will file it under
  "features that exist on paper".
- Fix: either wire it (section 6a) or delete the class, the function and the two OTel packages from
  the extra. Acceptance: a test that runs one investigation with an in-memory span exporter and
  asserts one trace containing API, step, tool and LLM spans; or the code is gone.

### E-11 [P2] Release artefacts: unhashed tools in the privileged job, non-reproducible build, tag not tied to main, image not published
- Evidence: `.github/workflows/release.yml:47-50` (job holds `contents: write`, `id-token: write`,
  `attestations: write`); `:57` (`pip install build==1.3.0`) and `:61` (`pip install cyclonedx-bom==8.0.1`)
  install without hashes and with unpinned transitive dependencies in that job; `pyproject.toml:2`
  (`setuptools>=68`, `wheel` unpinned, fetched at build time); no `SOURCE_DATE_EPOCH`;
  `release.yml:71-74` (the image is built, smoke-tested and discarded: not pushed, signed or attested);
  `scripts/release_check.py:63-76` checks that a CI run succeeded for the tagged SHA but not that the
  SHA is on `main` (a green pull-request head qualifies). The SBOM describes `requirements.lock`
  (`release.yml:62`), which is the dev-inclusive lock, not what the wheel installs.
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: provenance from `attest-build-provenance` on GitHub-hosted runners
  is SLSA Build L2-style, and it is undermined when the builder itself is installed unhashed in the
  job that holds the signing identity. "Signed" is true; "reproducible" is not claimed in code and
  cannot be demonstrated. The ADR 0003 exception for job-only tools is reasonable for a benchmark
  job; it is the wrong trade in the signing job.
- Fix: a small hashed `requirements-release.lock` for `build`, `cyclonedx-bom` and the pinned build
  backend; build in a job without `id-token`, pass `dist/` as an artifact to a signing job that
  installs nothing; set `SOURCE_DATE_EPOCH` from the tag commit and add a job that builds twice and
  compares SHA-256; have `release_check.py` require `git merge-base --is-ancestor <sha> origin/main`;
  generate the SBOM from the installed runtime environment. Acceptance: two independent builds of the
  tag give byte-identical wheel and sdist; a tag on a non-main commit is refused.

### E-12 [P2] Type checking is strict for three packages; the safety-critical ones are not
- Evidence: `pyproject.toml:96` (`strict = false`); strict flags apply to `ceap.domain.*`,
  `ceap.analytics.*` (`:104`) and `ceap.api.*` (`:120`) only. `policy`, `harness`, `llm`, `agents`,
  `persistence`, `mcp`, `paper`, `execution` are checked without `disallow_untyped_defs`. `\bAny\b`
  appears 1080 times in `src/ceap`. Ruff selects `E,F,I,B,UP,N,W` only (`pyproject.toml:88`): no `S`
  (bandit), `BLE`, `C90`, `T20`, `DTZ`, `ASYNC`, `PTH`.
- Status: VERIFIED (read); JUDGEMENT on priority
- Confidence: high
- Why it matters at a top firm: the plan promised strict for domain, analytics and api and delivered
  it. But the modules where a `None` or a wrong dict shape costs most (the policy engine, approvals,
  the tool dispatcher, the paper ledger) are the ones left on the default settings.
- Fix: add `ceap.policy.*`, `ceap.persistence.*`, `ceap.paper.*`, `ceap.execution.*` to the strict
  override next, then `harness`; add ruff `S`, `BLE`, `DTZ`, `ASYNC`, `T20` (with per-file ignores for
  the CLI modules) and `C901` with a ceiling set to today's maximum. Acceptance: `mypy src` clean with
  the wider override; `ruff check` clean with the wider selection.

### E-13 [P2] Hypothesis runs derandomised in the nightly too, so the nightly never explores new inputs
- Evidence: `tests/conftest.py:90-91` (`derandomize=True` in both the `pr` and the `nightly` profile).
  19 `@given` tests in the whole suite (15 under `tests/properties/`).
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: derandomised property tests are the right choice on pull requests.
  At night, 1000 fixed examples are the same 1000 every night: after the first green run the job
  finds nothing new, by construction. Nineteen properties for 43k lines is thin, and none covers the
  policy engine, the redactor, the request parser or the hash chain. The one nightly failure I could
  read includes a `FlakyFailure` in a property test, which means that test is not deterministic
  either.
- Fix: nightly profile with `derandomize=False`, `print_blob=True` and the example database uploaded
  as an artifact on failure; add properties for `redact` (idempotence is claimed at
  `src/ceap/util/redact.py:10`), `parse_question`, `canonical_json` round-trip, chain verification
  after any single-row edit, and policy decisions being monotone in role. Acceptance: two consecutive
  nightly logs show different seeds; the new properties exist and pass.

### E-14 [P2] The suite is sensitive to machine load, and the timeout change treats the symptom
- Evidence: `docs/testing/test-pyramid.md:66-72` (under load a research backtest exceeds the 30 s step
  timeout and tests that expect the full result fail "for that reason alone"; advice is to set
  `CEAP_STEP_TIMEOUT_SECONDS=90`); `tests/conftest.py:27-31` (`CEAP_TEST_TIMEOUT_SCALE`);
  `CHANGELOG.md:89-92` (seven 30-75 s tests moved to the 600 s tier). Collection alone took 124 s
  here. The 11-10 nightly hit the 1800 s ceiling on two tests.
- Status: VERIFIED for the documentation and the configuration; JUDGEMENT on soundness
- Confidence: medium
- Why it matters at a top firm: a 600 s ceiling still catches a true hang, so the change does not
  hide hangs. What it leaves in place is tests whose outcome depends on wall-clock time inside the
  code under test. Those are the tests that fail on a busy runner and get re-run until green. The
  nightly evidence says this is already happening.
- Fix: inject the step clock or deadline in tests (the harness already takes settings), so a test
  decides whether the step "times out" instead of the machine deciding; profile the 124 s collection
  (module-level dataset generation is the likely cause) and move it into fixtures; split the two
  seed-sweep tests so each stays far below its ceiling. Acceptance: the fast tier passes with
  `CEAP_STEP_TIMEOUT_SECONDS=1` where a full result is expected only through an injected clock;
  `pytest --collect-only` under 20 s.

### E-15 [P2] The benchmark gate does not cover the paths that were the performance findings
- Evidence: `benchmarks/test_bench_analytics.py:32-48` (four micro-benchmarks: VWAP, shortfall
  components, realised vol, rank correlation). Nothing for `synthetic._trades` (G2: 1.18x achieved
  against a 50x target, `CHANGELOG.md:106`), `simulate_backtest` (G4), `simulate_day`, the redactor on
  the logging path, or an API round trip. The job is not a required check (E-04) and compares means
  with a 25 % threshold on a shared runner (`.github/workflows/ci.yml:131-132`).
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: the gate exists, which is good, but it guards functions that were
  never slow. The generator miss is honestly disclosed; it is also unguarded.
- Fix: add benchmarks for the generator, one backtest, one paper day and `JsonFormatter.format`;
  compare `min` or median rather than mean; record results as an artifact per commit so a trend
  exists. Acceptance: the four new benchmarks appear in the job output and a deliberate 2x slowdown in
  `simulate_backtest` fails the job.

### E-16 [P3] Licence allowlist matches substrings, so "permitted" passes as MIT
- Evidence: `scripts/release_check.py:118` (`marker.lower() in lic.lower()`), markers at `:31-34`.
- Status: VERIFIED (ran the expression on "GNU GENERAL PUBLIC LICENSE v3: redistribution is permitted": true)
- Confidence: high
- Why it matters at a top firm: any distribution whose `License` field holds the full text, or the
  words "limited", "permitted" or "submit", passes. "LGPL" is also on the list while "GPL" is not,
  and the check runs over the dev environment, not the runtime closure.
- Fix: parse `License-Expression` as SPDX and compare identifiers exactly; fall back to exact
  classifier strings. Acceptance: unit tests with GPL-3.0 full text (refused) and `MIT` (allowed).

### E-17 [P3] Optional dependencies outside the lock and outside any extra
- Evidence: `src/ceap/rag/dense.py:88` and `:128` (`fastembed`), `src/ceap/paper/dividends.py:64`
  (`yfinance`); neither appears in `pyproject.toml` extras or `requirements.lock`.
- Status: VERIFIED (grep)
- Confidence: high
- Why it matters at a top firm: the opt-in reranker result and the dividend path depend on packages
  whose versions are recorded nowhere, which is the situation ADR 0003 exists to prevent.
- Fix: add `rag-dense` and `paper-data` extras and include them in the lock, or record the exact
  versions and hashes used next to the committed caches. Acceptance: `lock-fresh` covers them.

### E-18 [P3] `KillablePool` blocks the event loop while terminating a worker and depends on a private attribute
- Evidence: `src/ceap/mcp/cpu_pool.py:82` (`getattr(executor, "_processes", None)`), `:90`
  (`proc.join(timeout=5)`), reached from the coroutine at `:165` on cancellation; slot wait is a poll
  loop (`:156-158`), so waiters are not served in order.
- Status: VERIFIED (read)
- Confidence: medium
- Why it matters at a top firm: cancelling a CPU tool, the case this pool was built for, can stall
  the loop for up to 5 s per worker; if CPython renames `_processes` the kill silently becomes a
  no-op.
- Fix: run `_discard` through `asyncio.to_thread`; own the `multiprocessing.Process` objects directly
  instead of reaching into `ProcessPoolExecutor`; use an `asyncio.Queue` of idle slots. Acceptance:
  loop-lag assertion in the existing cancel test; a test that fails if no process was terminated.

### E-19 [P3] Key-strength estimate disagrees with the runbook and rejects a small share of correctly generated keys
- Evidence: `src/ceap/api/auth.py:30` ("43 x log2(94) = 281.8 bits"), `docs/RUNBOOK.md:134`
  ("43 x 6 = 258 bits"); `src/ceap/api/auth.py:103-110` (pool from character classes present). A
  `secrets.token_urlsafe(32)` value with no digit scores 43 x log2(52) = 245 bits and is refused
  (about 0.07 % of keys); one with no `-` or `_` scores 256.03, a pass by 0.03 bits.
  `principal_label` is an unsalted SHA-256 prefix of the key (`auth.py:100`) written to audit rows.
- Status: VERIFIED (arithmetic from the code; not executed)
- Confidence: medium
- Why it matters at a top firm: an estimator that depends on which characters happened to appear is
  not an entropy check, and two documents give different numbers for the same key.
- Fix: require a minimum length and alphabet (for example at least 43 URL-safe characters), drop the
  class arithmetic; derive the label with the per-process salt or require `id=`. Acceptance: 10,000
  generated keys all pass; both documents quote the same rule.

### E-20 [P3] Rate limiting covers two POST routes; authentication failures and reads are unthrottled
- Evidence: `src/ceap/api/routes.py:218` and `:500` are the only `_rate_limit` calls;
  `src/ceap/api/auth.py:212-220` returns 401 with no counter, delay or lockout; each attempt costs one
  HMAC per configured key (`auth.py:205-207`). The 429 body and a metric label carry the principal
  name (`routes.py:91`, `:94`).
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: with 256-bit keys guessing is not the risk; unauthenticated load and
  silent probing are. The limiter is also per process, which the module states.
- Fix: a small per-client-address bucket in the middleware for 401/403 responses, and a
  `ceap_auth_failures_total` counter with an alert rule. Acceptance: test that the 21st bad key in a
  second gets 429.

### E-21 [P3] Large functions without a complexity ceiling
- Evidence: `src/ceap/paper/fills.py:238` (`simulate_day`, cyclomatic complexity 50),
  `src/ceap/harness/engine.py:129` (`execute`, 38), `src/ceap/analytics/backtest.py:398`
  (`simulate_backtest`, 35), `src/ceap/agents/reporter.py:112` (`execute`, 23, in a 1072-line module).
  Ruff reports 47 functions over 10 and 23 over the statement limit when `C901`/`PLR0915` are enabled.
- Status: VERIFIED (ruff with extra rules)
- Confidence: high
- Why it matters at a top firm: the fill simulator and the backtest loop are where a wrong number
  would come from, and they are the hardest functions to review. The H6 split moved the harness into
  mixins (447 lines now) but its `execute` is still one 38-branch method.
- Fix: enable `C901` with `max-complexity` at today's maximum and lower it each release; extract the
  per-order fill decision and the per-rebalance step into pure functions with their own tests.
  Acceptance: no function above 25 by 0.11.

### E-22 [P3] Test files and identifiers named after releases and review rounds
- Evidence: 13 `test_cov_090_*.py` files and about 30 more named `test_r080_*`, `test_r090_*`,
  `test_r4_*`, `*_090.py` (for example `tests/unit/test_r090_durable.py`,
  `tests/integration/test_r4_platform.py`); `docs/testing/test-pyramid.md:15`, `:16`, `:25` put
  "0.9.0" or "19 + 0.9.0 additions" in the count column.
- Status: VERIFIED (listing)
- Confidence: high
- Why it matters at a top firm: a new maintainer cannot find the tests for approvals without knowing
  which release touched them, and the `cov_` prefix invites the reading "written for the number" even
  though the contents are sound.
- Fix: rename by subject (`test_approvals_ttl.py`, `test_durable_runs.py`) in one mechanical pull
  request; fill in the pyramid table with real counts generated by a script. Acceptance:
  `git ls-files tests | grep -E '_(r?0[0-9]{2}|r[0-9])_|cov_'` is empty.

### E-23 [P3] Documented coverage recipe omits `--cov-branch`; a CI step name still says 85 %
- Evidence: `docs/testing/test-pyramid.md:63-64` (no `--cov-branch`, yet the 93 % figure is described
  as branch coverage at `CHANGELOG.md:93`); `.github/workflows/ci.yml:66` ("floor 85 %") above a
  command with `--cov-fail-under=90` (`:74`). No `[tool.coverage]` section exists, so `branch = true`
  is not set anywhere but the CI command line. The 93 % itself is a local Windows measurement
  combined from two runs; no CI log with that figure is referenced.
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: someone who follows the recipe gets a line-coverage number and
  cannot reproduce the claim.
- Fix: add `[tool.coverage.run] branch = true, source = ["ceap"]` and a `[tool.coverage.report]`
  section to `pyproject.toml`; quote the CI figure with its run id. Acceptance: the recipe run locally
  prints a Branch column.

### E-24 [P3] Smaller items
- Evidence and fix, one line each:
  - `src/ceap/api/parsing.py:135`, `src/ceap/cli.py:357`, `src/ceap/ml/registry.py:214`: `date.today()`
    uses the host time zone; the product's sessions are London and New York. Use an explicit zone.
  - `src/ceap/harness/dispatch.py:66`, `src/ceap/mcp/execution/staging.py:179` and 17 more: `assert`
    as a runtime check in library code, removed under `python -O`. Raise explicitly.
  - `deploy/compose/docker-compose.yml:31` (`caddy:2.8-alpine`) and `.github/workflows/ci.yml:20`
    (gitleaks) are pinned by tag, not digest, unlike the base image.
  - `Dockerfile:22`: the project build pulls `setuptools`/`wheel` unhashed through build isolation.
  - `src/ceap/api/app.py:125`: `app = create_app()` at import configures logging and reads the
    environment as an import side effect.
  - `requirements.lock:1196` and `:1274`: numpy 2.4.6 on Python 3.11, 2.5.3 on 3.12; coverage and
    goldens are exercised with coverage on 3.12 only. State which interpreter the committed numbers
    come from.
  - Two Dependabot pull requests (#12 actions, #13 `mcp` to `<3`) are open with green CI; #13 widens a
    cap the project keeps on purpose and should be closed with an ignore rule.
  - `ruff format --check` is in pre-commit but not in CI.
- Status: VERIFIED (read)
- Confidence: high
- Why it matters at a top firm: none is serious alone; together they are the polish a reviewer uses
  to judge care.
- Fix: as listed. Acceptance: each has a one-line test or lint rule.

## 5. Earlier plan (2026-11-09) items for this area

Done and convincing:

- H1 mypy per-module strict for domain, analytics, api; no global `ignore_missing_imports`
  (`pyproject.toml:101-138`). Scope is as promised; E-12 argues it should now widen.
- H2 branch coverage with a 90 % floor in CI and nightly (`ci.yml:74`, `nightly.yml:38`). The
  per-package 95 % for analytics and domain was not done (the plan table already says so).
- H4 Windows fast-tier job (`ci.yml:78-101`). Not a required check (E-04).
- H5 repo-integrity tests in `tests/repo/`. H9 one marker definition each (`pyproject.toml:75-80`).
- H7 Dependabot, CodeQL, Scorecard, SBOM, provenance, cosign: all present and the v0.9.0 release run
  succeeded. Weaknesses in E-11.
- H10 label gate extended to snapshots and cassettes (`ci.yml:302-316`). Not required (E-04).
- H11 key entropy check and key ids. Works for raw keys; see E-02 and E-19.
- H12 broad excepts: 25 `except Exception` remain; I checked one (`src/ceap/mcp/cpu_pool.py:86`, with
  a stated reason) and took the enforcing test on trust. Prints remain only in CLI and report-driver
  modules (59, of which 40 are in the three `cli.py` files).
- H14 pre-commit. I1 ADRs (five). G13 two-stage image without pip.

Done but weak:

- H3 mutation testing: configuration only, never executed, baseline null (E-01). The two named
  mutants are killed by `tests/properties/test_r090_mutation_gaps.py`.
- H8 timeouts and tiers: in place; the nightly is red on timeouts (E-01, E-14).
- H13 benchmark gate: present, guards four micro-functions, not required (E-15).
- H15 Schemathesis: job defined, never run, GET only, 50 examples (E-01). OpenAPI diff in `contracts`
  is done and required.
- H6 harness split: the module is 447 lines, but `execute` is still complexity 38 and the mixins
  share state through a base class (E-21).
- G1/G7 off-loop work: dataset generation moved off the loop; SQLite calls did not (E-06).
- G3 killable process pool: works in design; E-18.
- G13 reverse-proxy compose example: present and cannot start (E-02).

Not done:

- H2 second half: 95 % on analytics and domain.
- G2 generator speed: 1.18x against 50x, disclosed.
- Anything that enforces layering (was not in the plan; E-05).

## 6. Candidates: present now, value, how to measure, cost, verdict

All local and free.

**(a) OpenTelemetry tracing end to end with a local collector.**
Present now: an in-memory tracer with spans per step and tool; the OTel class exists but is never
constructed (`src/ceap/observability/tracing.py:136`, `:155`; E-10). No context propagation into MCP
subprocesses or CPU workers, no `traceparent` handling in the middleware
(`src/ceap/api/middleware.py:58-60` handles `X-Request-ID` only).
Value here: moderate. The in-memory trace already answers "what did this task do". OTel adds one
timeline across API, harness, tool process and Ollama call, which is what explains the load-sensitive
timeouts in E-14. It also removes a dead feature.
How to measure: one investigation against a local `otel-collector` with the file exporter (or Jaeger
all-in-one in Docker); assert one trace id from the HTTP span to the LLM span; report p50/p95 per span
kind for the 50 scenarios.
Cost: 3 plan-days (provider set-up, FastAPI and httpx instrumentation, propagation through
`run_in_executor` and the stdio MCP client, a test with an in-memory exporter).
Verdict: **adopt later** (0.10 or 0.11), but **fix E-10 now** by wiring `build_tracer` or deleting it.

**(b) Evaluation regression gates in CI without a GPU.**
Present now: recorded cassettes replayed in the PR tier, golden sets with a label gate, contract and
drift monitors in the `lint` job (`.github/workflows/ci.yml:44-47`). No gate compares headline metrics
with the previous release.
Value here: high. This is the control a model-validation group looks for.
How: three layers, none needing a model in CI. (1) Deterministic metrics (cause recall on the 50 + 30
scenarios with the mock model, retrieval R@3, injection obey rate on the recorded corpus) are
recomputed in CI and compared with a committed `eval_baseline.json` under stated tolerances. (2)
Cassette replay recomputes every model-dependent metric from recorded replies, so a change in
parsing, scoring or prompts that would alter the score fails without calling a model; a prompt
change makes the cassette stale, which must fail and force a local re-record (the stale detection
exists in `src/ceap/llm/recording.py:222`). (3) The committed result JSON carries model digest, prompt
hash and corpus pin; CI checks the triple against the code.
Cost: 3 plan-days.
Verdict: **adopt now**.

**(c) Self-hosted runner for nightly local-model evaluation.**
Present now: nothing; local-model runs are manual on the owner's laptop.
Plainly: **this is a bad idea on a public repository.** A self-hosted runner executes whatever a
workflow tells it to. On a public repository a fork pull request, or a compromised action or
dependency, can run code on that machine, which here is the owner's personal Windows laptop with
credentials, the paper-trading state and other repositories on it. GitHub's own guidance is not to
attach self-hosted runners to public repositories. Restricting triggers to `schedule` reduces the
exposure but leaves a persistent, non-ephemeral runner that any future workflow edit can target.
Free alternative: a local scheduled task (Windows Task Scheduler) that runs the evaluation, writes
the result JSON and cassettes, signs them, and pushes them to a `local-eval` branch or opens a pull
request; CI then validates the artefacts by replay as in (b). The machine pulls and pushes; nothing
on GitHub can start a process on it.
Cost: 1.5 plan-days for the scheduled-task variant.
Verdict: **self-hosted runner: do not adopt. Scheduled local run with replay verification: adopt later.**

**(d) Fuzzing parsers and the API.**
Present now: Schemathesis job, GET only, never run (`.github/workflows/nightly.yml:65-87`); 19
Hypothesis properties, none on parsers.
Value here: high for Hypothesis, low for atheris. The untrusted-input surfaces are
`src/ceap/api/parsing.py` (free-text question to symbol and window), `src/ceap/util/redact.py`,
`parse_key_hashes` (`src/ceap/api/auth.py:153`), the JSON plan and tool-argument parsers in
`src/ceap/llm/schemas.py`, and knowledge-document ingestion. All are pure Python; a coverage-guided
native fuzzer buys little over Hypothesis, and atheris adds a compiled dependency that does not
install cleanly on Windows.
How to measure: properties "never raises anything but the documented error type", "redact is
idempotent", "parse then render then parse is stable", a regex-backtracking bound (time per 10 kB
input) for the redactor and the phone pattern; Schemathesis extended to POST with an auth key and the
stateful mode against an in-memory database. Report the number of distinct failures found on first run.
Cost: 2.5 plan-days.
Verdict: **Hypothesis on parsers and Schemathesis with POST: adopt now. atheris: not worth it.**

**(e) SLSA provenance level and reproducible builds.**
Present now: `attest-build-provenance` and cosign bundles for wheel, sdist and SBOM
(`.github/workflows/release.yml:63-70`). That is provenance from a hosted builder, about SLSA Build
L2. Not L3: build and signing share a job that installs unhashed tools (E-11). Not reproducible:
unpinned build backend, no `SOURCE_DATE_EPOCH`. The image is unsigned and unpublished.
Value here: moderate. For a project with no downstream installers the signature is mostly a
demonstration, so it should be a correct one.
How to measure: build the tag twice in separate jobs and compare SHA-256; `gh attestation verify` and
`cosign verify-blob` as a post-release job; document the verification command in the README.
Cost: 2 plan-days (hashed release lock, split build and sign jobs, reproducibility job, verify job).
L3 through the SLSA generic generator's isolated reusable workflow: 1 more day.
Verdict: **reproducible build and split jobs: adopt now (cheap, closes E-11). Formal L3: later.**

**(f) Splitting the package or enforcing layering with import-linter.**
Present now: no enforcement; 15 mutual package pairs (E-05).
Value here: high for import-linter, negative for splitting. Separate distributions would multiply
the release and lock machinery for one maintainer and fix nothing a contract does not.
How to measure: count of contract violations today (the exception list) and per release; number of
function-local `ceap.*` imports (228 today).
Cost: 1 plan-day to add contracts with the current exceptions; about 4 plan-days to remove the worst
ten violations.
Verdict: **import-linter: adopt now. Package split: not worth it.**

**(g) What else a senior platform engineer at a bank or HFT firm would expect.**

- A deployment smoke test: build the image, start it with the compose file, call `/health`, run one
  investigation, check the knowledge base and model hashes. Present: `ceap --help` only. Cost 1 day.
  **Adopt now** (it would have caught E-02 and E-03).
- An installed-wheel test job (clean venv, outside the repository). Cost 0.5 day. **Adopt now.**
- Nightly failure alerting (issue on failure) and a nightly status badge. Cost 0.25 day. **Adopt now.**
- A real `[tool.coverage]` section with per-package floors (analytics and domain at 95 %) and
  `coverage json` uploaded per run so the number has a history. Cost 0.5 day. **Adopt now.**
- Diff coverage on pull requests (`diff-cover`, job-only tool). Cost 0.25 day. **Later.**
- SLOs measured rather than declared: request latency and event-loop-lag histograms exist; a load
  test (`locust` or a 50-line asyncio script) that produces p50/p99 for task submission with 4
  concurrent investigations is missing. Cost 1.5 days. **Later.**
- Graceful shutdown under load and SIGTERM in the container (drain running tasks, checkpoint WAL)
  tested end to end. Present: lifespan cancels tasks (`src/ceap/api/app.py:101-110`); no test with a
  real signal. Cost 1 day. **Later.**
- Database migration tests against a copy of the previous release's file (upgrade from 0.8.0 schema
  with data), plus a documented restore drill. I did not check whether this exists. Cost 1 day. **Later.**
- Configuration as one typed, documented object with a `ceap config check` command that prints the
  effective settings with secrets masked and fails on unknown `CEAP_*` variables. Present: env reads
  spread over `config.py`, `ratelimit.py`, `auth.py`, `app.py`, `cpu_pool.py`. Cost 1.5 days. **Later.**
- Zizmor or actionlint on the workflows (job-only, free). Cost 0.25 day. **Adopt now.**
- Not worth it here: Kubernetes manifests, Helm, a service mesh, a second database, distributed rate
  limiting. The product is a single-process demonstration and should say so once, clearly.
