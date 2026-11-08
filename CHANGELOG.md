# Changelog

Every pull request adds a line under `## Unreleased` (CI enforces it unless the PR is labelled
`no-changelog`). At release time the section is renamed `## [X.Y.Z] - date`; the release gate
refuses a tag whose version has no section.

## Unreleased

### R3C wiring: 3C.3 retrieval in the planner and policy, 3C.4 two-call reporter with citations

Label: `contracts-change` (`domain/evidence.py`: `document_citation`; `LLMResponse.citations`;
the knowledge tools' input schema gains `classifications`). No live model call (D15).

- **Breaking:** the knowledge query is built by `ceap.rag.query.retrieval_query` from the parsed
  question (domain vocabulary only), symbol, window and cause candidates; the planner replaces
  whatever query the model wrote and pins `classifications` to the principal's roles.
  `KnowledgeBase.search` scores `"; "`-separated segments separately and merges them round-robin.
- **Breaking:** `knowledge.search_documents` and `knowledge.get_document` take `classifications`
  (comma-separated) and default to `public,internal`; a new policy rule
  (`knowledge-classification`) denies a request for more than the roles allow. Viewers can no longer
  read `restricted` documents (`04_trading_limits.md`).
- **Breaking:** investigation reports are written in two calls: structured claims
  (prompt **reporter_claims@1**), validated against facts, evidence ids and numbers; then prose with
  citations (prompt **reporter_prose@1**) over one `document` block per retrieved chunk with
  `cache_control` on the last. Each citation's quoted text must equal the chunk sent; verified
  citations become `DOC` evidence with the narrative span, document version and corpus hash, a
  fabricated quote is a blocking `NARRATIVE_UNTRACEABLE`. The narrative ends with the knowledge
  corpus hash. New report fields under `critique.knowledge`.
- `AnthropicLLMClient` returns `content_block_location` citations with their offsets in the joined
  text (`LLMResponse.citations`); the mock and the hand-built cassettes cite verbatim.
- Contract snapshots: `wire_request_claims.json`, `wire_request_prose.json` (new),
  `mcp_tools.json`, `wire_request_planning.json` and the ten investigation cassettes regenerated.


### R3B: agents and agentic AI, within the boundary (PLAN section 2, items 3B.1-3B.9)

No live model call anywhere (D15); no code-writing agent (D13); case memory per D10.

#### Breaking / migration

- **Agents no longer fetch their own data.** `BaseAgent.fetched()` reads what the plan fetched and
  never invokes a tool; `ensure` remains only where arguments depend on fetched data (the risk
  agent's limit check). The harness appends, before the first agent step, every tool call a chosen
  agent declares in `ceap.agents.requirements` (`gov-data-NN` steps, validated and policy-checked
  like the plan). An agent whose data step failed fails with `PlanDataMissingError`.
  Mechanical signature change in `agents/quant.py`, `agents/execution.py` (R2T lane) and
  `agents/alpha.py`, `agents/market.py`, `agents/engineering.py` (R2B lane): `self.ensure(` ->
  `self.fetched(`, no logic change.
- **The critic model can no longer change a confidence.** A dispute counts only when it names an
  alternative deterministic check (`check`: `alternative_baseline`, `different_horizon`, `peer_set`)
  whose result contradicts the finding's cited value; the harness runs the check through the policy
  path (at most 3 per task) and caps an overturned finding at 0.3. `supported=False` without a check
  is recorded (`unbacked_disputes`) and changes nothing. `critique.overall` no longer includes model
  text; it is stored separately as `critique.llm_overall`.
- Prompt changes: **planner@3** (scope within enumerated choices: agents, baseline, ranked
  hypotheses, prior cases; data blocks) and **critic@3** (disputes need a named check).
  `report@1`, `research_planner@2` and `research_report@1` are unchanged.
- contracts change (additive): `HypothesisRow` gains `test`, `evidence_ids`, `rank`, `reason`;
  `domain/agents.py` gains `AgentMessage` (envelope version 1) and `parse_message`;
  `CritiqueAssessment` gains `check`; the plan output schema gains optional `baseline` and
  `hypotheses`; `ApprovalDecision` gains `action`; `QueuedApprovalGateway.decide` gains keyword-only
  `action`.
- Tool evidence records carry `values` (top-level scalar fields of the result) so findings can cite
  `values.<field>`; knowledge results are `DOCUMENT` evidence.
- `POST /approvals/{id}`: a reject or modify decision needs a reason in `comment` (422 otherwise);
  `action: modify` means not approved, changes requested.

#### Added

- 3B.1 planner owns scope within enumerated choices: which agents run, which optional tools, which
  baseline (`task_baseline`, `prior_equal_length`, `prior_double_length`; each distinct choice is
  a trial recorded in `agent_outputs.scope.trials`), which pre-registered hypotheses to rank. A
  baseline outside the set is rejected by the schema, semantic validation and the sanitiser.
  `ensure` call sites fall from 28 to 1.
- 3B.2 `analytics/hypotheses.py`: each hypothesis maps to a pre-registered deterministic test; the
  model only ranks; the harness builds `InvestigationReport.hypotheses` and the report shows
  supported, refuted and untested hypotheses, each with its own evidence.
- 3B.3 critic disputes through alternative deterministic checks (above).
- 3B.4 evidence binding: findings carry `evidence_refs {id, path, value}` (bound by the harness from
  the attribute the agent cited); validation drops a finding whose cited value does not match the
  value at its path (`EVIDENCE_VALUE_MISMATCH`) or whose calculation derives from unknown evidence.
- 3B.5 injection hygiene: the question, tool descriptions, prior cases, evidence (logs, deployment
  text) and retrieved policy text reach a model only inside `<data surface=... id=...>` blocks that
  cannot be closed from inside; threat model section per surface; `tests/adversarial/test_surfaces.py`.
- 3B.6 typed agent messages (`AgentMessage`, drifted versions rejected), one bounded re-plan on a
  tool or required-agent failure with the failure reason, and a per-call context budget with a
  documented truncation order (`ceap.llm.prompts.TRUNCATION_ORDER`).
- 3B.7 case memory (D10): after a human decision the harness appends a compact case record; the
  planner receives the three nearest cases of the same dataset as read-only context; append-only,
  audited, partitioned by dataset, no memory tool; OFF in evaluation (`evaluation_platform()`,
  `case_memory.reads == 0` reported). Investigations accept `signoff: true` (optional human sign-off).
- 3B.8 approval review payload: hypothesis table, evidence links, diff against the pre-trade
  estimate (the baseline window until a pre-trade cost model lands) and the critic's disputes;
  `GET /approvals/{id}`; the reject / modify reason is in the hash-chained audit entry and the case record.
- 3B.9 stdio MCP lifecycle: each connection's `AsyncExitStack` is owned by a dedicated task; one
  reconnect when the child exits (test marked `slow`).

### R3A: LLM layer (PLAN section 2, items 3A.1-3A.8)

Round R3A of the 2026-11-07 plan (LLM layer, offline-provable). No live model call anywhere (D15).

### Breaking / migration

- `ceap.llm.client.extract_json` is deleted: model output is validated against strict schemas
  (`ceap.llm.schemas`) and there is no lenient JSON recovery. Fake clients must return the whole
  reply as one JSON document (or set `LLMResponse.parsed`).
- `AnthropicLLMClient` sends the system prompt as a text block with `cache_control`, marks every
  tool `strict: true`, and uses `messages.parse()` when `LLMRequest.output_model` is set. Without
  an explicit model each purpose now uses its own pinned model (previously the narrative model).
- contracts change (`domain/reports.py`, additive): `LLMCallRecord` gains `task_id`, `purpose`,
  `prompt_id`, `prompt_version`, `stop_reason`, `usd_is_estimate`, `redacted_request`;
  `InvestigationReport` gains `prompts`.
- `LLMRequest` gains `output_model`; `LLMResponse` gains `parsed`, `parse_errors`,
  `cache_read_tokens`, `cache_write_tokens`, `request_id`.
- Prompt changes: planner@2, research_planner@2 and critic@2 describe the schema-enforced reply
  instead of a hand-written JSON shape; report@1 and research_report@1 are unchanged in text and
  now live in `llm/prompts/*.md`.

### Added

- 3A.1 structured outputs for planner and critic: plan steps are a union over the read-only tool
  catalogue generated from MCP input schemas; semantic validation (windows, symbol, universe,
  agents, governance duplicates, step count, finding ids, confidence range) is the target of
  exactly one repair, then `DEGRADED`; schema-subset lint. The planner sanitiser now also drops
  tool steps whose window is not a task window.
- 3A.2 wire contract tests over the real `AsyncAnthropic` with an in-memory transport, a committed
  request snapshot (`tests/contract/snapshots/`) and full-format responses validated by the pinned
  SDK types.
- 3A.3 nine adversarial fake clients (`tests/fakes/clients.py`).
- 3A.4 ten harness invariants as property tests under every fake client (100 examples on PR,
  1000 nightly). The narrative-audit invariant becomes blocking when the R3A engine patch lands.
- 3A.5 provenance-tagged cassettes (`ceap.llm.recording`, `tests/cassettes/`): `STALE` replay
  through the real SDK, scrubber on write, `assert_publishable` refuses model-behaviour metrics
  from `hand_built` cassettes; `llm_provenance` labels evaluation output `NOT RUN LIVE`.
- 3A.6 per-call `LLMCallRecord` with a redacted body, per-task cost spans attributed through a
  context variable (`task_scope`, `task_cost`), `CEAP_TASK_BUDGET_USD` enforced before the call
  from a labelled local token estimate, pinned list prices in `model_pins.toml`.
- 3A.7 prompt registry (`llm/prompts/*.md`, sha256 of canonical bytes, version and hash in
  request metadata, call records and reports; golden hash snapshot plus CHANGELOG check).
- 3A.8 adapter equivalence on the ten scenario templates (mock path vs adapter-plus-cassette
  path, structural diff empty apart from narrative). Adapter equivalence, not model fidelity.

### R4: platform and persistence (PLAN section 2, items 4.1-4.9)

#### Breaking / migration
- **Database file.** Tasks, results, approvals, staged orders, LLM call records, the trial ledger,
  idempotency keys and a hash-chained audit log now live in SQLite (`CEAP_DB_PATH`). Default:
  `:memory:` when `CEAP_ENV=dev` (tests and demos leave no file), `var/ceap.db` everywhere else.
  The schema migrates forward on start; a file at an older version is first copied to
  `<db>.v<N>.bak` with the online backup API; a file newer than the code refuses to open. Mount
  `var/` on a volume in containers. New commands: `ceap db status|migrate|backup|verify-chain|prune`.
- **New `auditor` role** (`tasks:read-any`, `metrics:read`, read capabilities only). Reads are now
  scoped to the owner: a viewer (or any role without `tasks:read-any`) gets 404 for another
  principal's task, result, report or trace, and lists only its own. `auditor`, `approver` and
  `admin` read everything. Dev key `dev-auditor-key` added.
- **`Idempotency-Key` header** on `POST /investigations` and `POST /research`: scoped per
  principal, persisted, 24 h TTL; the same key with the same body returns the first response
  (same `task_id`), with a different body 422.
- **`/health` is liveness only** (`status`, `version`). The previous fields moved to the
  authenticated `GET /status` (`tools:read`). **`/metrics` needs a key** with `metrics:read`
  (engineer, auditor, admin).
- **Metric names in the exposition**: counters always end in `_total`; durations recorded in ms
  are exported in seconds (`ceap_tool_latency_ms` -> `ceap_tool_latency_seconds`), and a summary's
  maximum is a separate gauge `*_seconds_max`. Update dashboards.
- **Request limits**: body over `CEAP_MAX_BODY_BYTES` (64 KiB) -> 413; `gross_notional` above 10 bn
  -> 422; MCP `paginate` clamps `limit` to [1, 1000] and `offset` to the list.
- `domain/common.py` (contracts-change): `utc_now()` and `new_id()` honour an injected clock / id
  source held in context variables (`CLOCK`, `ID_SOURCE`); unset, behaviour is unchanged.

#### Added
- 4.1 `ceap.persistence`: stdlib `sqlite3` behind one writer thread (`Database.run` / `arun`),
  WAL, `busy_timeout`, `PRAGMA user_version` forward-only migrations (`migrations/0001_initial.sql`,
  `0002_platform.sql`), backup before migrate, audit hash chain over canonical bytes
  (`ceap db verify-chain`), retention for results, traces and raw model I/O
  (`CEAP_RETENTION_*_DAYS`) and a size bound (`CEAP_DB_MAX_BYTES`, oldest results pruned first; the
  audit log is never pruned).
- 4.2 Restart semantics: on start, tasks left `PENDING`/`RUNNING` become `INTERRUPTED` and pending
  approvals `EXPIRED` (audited). A decision is a conditional `UPDATE ... WHERE status='PENDING'`:
  of two concurrent decides exactly one wins; the loser and any late decide get 409 and an audit row.
- 4.4 A task's concurrency slot is released while it waits for a human, so four tasks awaiting
  approval no longer block a fifth; `CEAP_APPROVAL_TIMEOUT_S` (default 120).
- 4.5 The staging approval carries `portfolio_hash` of the portfolio the approver saw; the harness
  passes it to `execution.stage_orders` (`approved_portfolio_hash`), which recomputes the portfolio
  and refuses on a mismatch (TOCTOU closed).
- 4.6 API keys held as salted HMAC-SHA256 digests; `CEAP_API_KEY_HASHES` takes pre-hashed keys with
  optional expiry (`sha256$salt$digest:role[:YYYY-MM-DD]`); raw keys never in reprs, logs or errors;
  `X-Request-ID` correlation id on every response and every log line; one access-log line per request.
- 4.7 Task replay bundles: `ceap replay BUNDLE --record --dataset T01` writes inputs, tool outputs,
  redacted model I/O, prompt hashes, approval decisions and the clock seed; `ceap replay BUNDLE`
  re-runs offline under an injected clock and checks the result byte for byte (exit 1 on drift,
  naming the first differing path).
- 4.8 Prometheus exposition passes `promtool check metrics` rules (HELP/TYPE, `_total`, base units);
  HTTP metrics `ceap_http_requests_total`, `ceap_http_errors_total`,
  `ceap_http_request_duration_seconds`; an OpenTelemetry span test.
- 4.9 `tests/properties` (hypothesis: VWAP within [low, high], IS components sum, fully-filled IS =
  execution cost, adverse cost >= 0, impact non-negative and monotone in participation, weights sum
  to 1 and dollar-neutral; 100 derandomised examples on PR, 1000 nightly), `tests/contract` (MCP
  schema contract + committed catalogue snapshot), `tests/golden` (research report goldens under the
  replay clock; TCA goldens are regenerated at 0.5.0 integration after the R2T re-baseline).

#### Fixed
- `Settings` repr no longer prints `api_keys` or `ANTHROPIC_API_KEY`; the duplicate-key error no
  longer echoes the key.
- Span start times are late-bound to `time.perf_counter`, so an injected clock reaches them.

### R2T - TCA realism (2T.1-2T.8)

- Session clock is US regular hours (09:30-16:00 America/New_York, `ceap/data/session.py`) with a
  U-shaped intraday profile; baselines are the same clock window on the 5 previous sessions.
- Perold IS with delay, spread, timing, opportunity and fee legs (sum to total to 1e-9); IS
  standard error from the schedule; deterioration is residual z >= 1.96 against a pre-trade
  estimate (parametric square-root model with split-conformal 90 % intervals, `ceap/ml`).
- Size-weighted effective/realised spreads, price improvement, markouts (1/10/60/300 s).
- Peer-relative effects with bootstrap CI (no causal claim); multi-day TCA universe, broker/algo
  comparison, four-eyes outlier queue and `ceap tca-report`.
- **contracts-change:** `Order` gains `decision_time`, `adv_shares`, `sigma_daily_bps`, `broker`;
  `ExecutionMetrics` gains IS components, pre-trade and markout fields (all defaulted);
  `VenueStatistics.volume_share` renamed `market_volume_participation` (alias kept);
  `Trade.condition` added.
- Approved tz edits outside this lane: `platform.py` and `api/parsing.py` take the session tz from
  `ceap.data.session`; `agents/reporter.py` report title says New York.

Re-baseline (50 synthetic scenarios, mock LLM):

| Metric | 0.4.0 | R2T |
|---|---|---|
| Primary-cause accuracy | 1.00 | 0.98 |
| Cause coverage | 1.00 | 0.98 |
| False-positive rate | 0.02 | 0.06 |
| Completed | 1.00 | 1.00 |
| Number-audit warnings | 0 | 0 |
| T01 benign market impact | about -45 bps | 2.3 bps (mean, 200 seeds) |
| T01 false-flag rate | n/a | 0.01 (200 seeds) |
| Broker 3 bps gap power | n/a | 0.83 (100 seeds) |
| Broker test size (no gap) | n/a | 0.05 (100 seeds) |

The scenario numbers fall slightly because the generator is harder (U-shape, fees, delay, odd
lots and late prints) and attribution now needs a statistically significant residual.

### Round R3C (retrieval half: 3C.1, 3C.2, 3C.5)

- **Breaking:** knowledge retrieval is Okapi BM25 (stdlib) instead of hashed TF-IDF;
  `ceap.rag.embeddings` (`Embedder`, `HashingEmbedder`) is removed and `tokenize` moved to
  `ceap.rag.text`. The knowledge tool no longer calls itself "semantic search". `KnowledgeBase`
  takes `chunking` and `min_support` instead of an embedder; `search` gains `classifications` and
  `min_support` and returns nothing when no chunk clears the support gate ("no policy found").
- Chunks and hits carry document, section, version, effective date and classification; the
  knowledge tool returns a SHA-256 `corpus_hash`.
- Three chunking strategies (`section`, `sentence`, `fixed` with overlap) compared by a retrieval
  eval (`data/reference/knowledge/eval.json`, 20 train / 10 held-out / 10 negative queries written
  before tuning; `python -m ceap.rag.evaluation`). Held-out recall at 3 is 1.00 and MRR 1.000;
  CI floor 0.80 / 0.70; all negatives empty.
- Corpus growth: front-matter versioning on every knowledge document, a new
  `09_attribution_thresholds.md` (kept equal to `Thresholds` defaults by a test) and
  `docs/quant/quant.md` via `manifest.json`.

- R2B (ML, D12): change-point detection on per-minute technology and feed metrics (robust z, CUSUM, PELT; `ml/anomaly.py`) with an alarm threshold fitted to a stated cost and ARL0 >= 20 sessions, consumed by the engineering and market agents as change-point findings that name the deployment lag.
- R2B: ridge signal combination with purged, embargoed walk-forward CV; every penalty and single signal is a ledger trial; the out-of-sample deflated Sharpe is reported next to each signal (`analytics/ml_signals.py`, MCP tool `alpha.combine_signals`).
- R2B: dynamic leakage tests (perturbation and shuffled-label) for every signal and feature; a planted look-ahead fails both (`analytics/leakage.py`).
- R2B: held-out isotonic confidence calibration for the change-point finding: Brier 0.0110 raw vs 0.0094 calibrated on seeds 100-199, so it ships. Added the `BaseAgent.calibrated` hook; the other confidences are now documented as heuristic.
- R2B: model registry and cards (`ml/registry.py`, `data/models/`, `docs/ml/`): loaded by hash, labelled "fitted to synthetic generator <hash>", PSI drift warning at load, and a nightly reproduction test across thread counts.

## [0.4.0] - 2026-11-07

Rounds R0, R1 and R2 of the 2026-11-07 plan, shipped as one combined PR (AshJha0/AgenticAICashEquities#1).
CI green on the combined head e915c8a: run 42396076903 (all 9 jobs).

Round R0 of the 2026-11-07 plan (`docs/review/2026-11-07/PLAN.md`): foundations for 0.4.0.

### Breaking / migration

- `requirements.lock` is now a universal, hashed lock produced by `uv pip compile --universal
  --generate-hashes`. Install it with `pip install --require-hashes -r requirements.lock` (the new
  `scripts/bootstrap.sh` and `scripts/bootstrap.ps1` do this). The old instructions to hand-edit the
  lock after regenerating it are gone.
- The `ml` extra no longer pulls `xgboost` (it was never imported).
- The `platform` test fixture is function-scoped; tests that relied on state left by an earlier
  test must create it themselves.
- `Task.create` redacts emails, API keys, tokens and account-like numbers from the question text.

### Added

- Lock refreshed for pip-audit findings: certifi 2026.10.23 (PYSEC-2026-4320) and
  opentelemetry-api/sdk 1.45.2 (CVE-2026-114603). `.gitleaks.toml` extends the default rules and
  allowlists only the documented `dev-*-key` demo keys.

- `src/ceap/util/canonical.py`: canonical bytes for every hash (LF, sorted-key JSON, UTC datetimes
  in one format, NaN refused). `.gitattributes` makes every text file LF in the repository.
- `src/ceap/util/redact.py`: redaction for question text and log lines, and a cassette scrubber
  that removes `x-api-key`, request ids and organisation ids.
- Domain contracts frozen for later rounds: `TaskStatus` (with `DEGRADED`, `INCOMPLETE_ANALYSIS`,
  `INTERRUPTED`), `ResultWarning`, `EvidenceRef` and `Finding.evidence_refs`, `HypothesisTable`,
  `LLMCallRecord`, `schema_version` on `ToolResult` and `InvestigationReport`, and
  `ApprovalRequest.content_hash`. All additive with defaults.
- CI: least-privilege permissions, a concurrency group, actions pinned to commit SHAs, pip cache,
  a Python 3.11 and 3.12 matrix, pytest-xdist, a coverage floor of 80 %, `lock-fresh`, `pip-audit`,
  gitleaks with a planted-key self-test, a Docker build and `ceap --help` smoke test, a CHANGELOG
  check and a domain-contracts check (`scripts/check_contracts.py`, label `contracts-change`).
- `nightly.yml`: every test tier in random order with 1000 hypothesis examples per property.
- `release.yml` and `scripts/release_check.py`: a tag releases only when every check on the commit
  is green, the version agrees in `pyproject.toml`, `ceap.__version__` and the tag, the CHANGELOG
  has the section, `scripts/check_banned_words.py` is clean and every dependency licence is on the
  allowlist.
- Test markers `slow` and `nightly`; hypothesis profiles (`pr` 100 examples, `nightly` 1000, both
  derandomised).

### Changed

- COOKBOOK (recipe 34), the architecture overview and the diagrams page (section 10, CI and the release gate) describe the
  new workflow.

### R1: fail closed by default (PLAN section 2, items 1.1-1.11)

- **Risk-free series committed** (`data/reference/rf.csv`, Ken French daily RF, 2000-01-03 to
  2026-09-30, attribution header). The null sweep was re-run with it: the hurdle moved from
  t = 0.65 (rf = 0, hash `dfdadabe...`) to **t = 0.40** (hash `ef474869...`), because long-only noise
  configurations now pay rf and clear the Sharpe gates less often.
- **Working orders.** A fill clipped by the 10 %-of-ADV cap now leaves a working order that is
  traded on every following session at the cap until it completes or the next decision replaces
  it (`working_order` events). Before, the residual waited for the next rebalance, so at 250-500 M
  the book silently under-traded and its Sharpe flattened: with rf the 250 M and 500 M Sharpe
  ratios tied within 0.002 (Spearman -0.943 against the plan's -1). The acceptance was not
  changed; with the fix R03 is strictly monotone again (0.63 at 10 M to -0.84 at 500 M, gap 1.46
  against two standard errors of 0.46), as are R01, R08 and R09.
- **Installed wheel.** `data/` is not shipped: rf falls back to 0 (stated), the NYSE calendar is
  regenerated from its rules, and the hurdle falls back to the committed value in code (a test
  keeps it equal to the thresholds file).

#### Breaking / migration

- **`CEAP_LLM_PROVIDER=auto` is removed.** It silently used the mock when no key was set. Set
  `CEAP_LLM_PROVIDER=anthropic` (needs the `llm` extra and `ANTHROPIC_API_KEY`) or
  `CEAP_LLM_PROVIDER=mock`. `auto` now fails at startup with a message naming both. Outside
  `CEAP_ENV=dev` the variable must be set explicitly; in dev it defaults to `mock`.
- `CEAP_LLM_PROVIDER=anthropic` with a client that cannot be built (no SDK, bad key) raises
  `SettingsError` at startup instead of falling back to the mock.
- Model ids must be pinned in `src/ceap/llm/model_pins.toml`: `claude-opus-5-5` (planning),
  `claude-sonnet-5-5` (narrative, critique), `claude-haiku-5-5` (reserved for judge paths). The
  old default `claude-sonnet-4-5` (retires 2026-11-30) and any unpinned id are refused.
- `LLMRequest.temperature` is removed; `max_tokens` defaults to the pinned value per purpose
  (planner 16000, critic 8000, reporter 8000) to leave room for adaptive thinking.
- Four-eyes approvals: new `approver` role holds `approvals:decide`; `admin` loses it (admins
  keep cancel-any through the new `tasks:cancel-any` capability). Nobody may decide an approval
  they requested (403), and the staging approval must come from a different principal than the
  proposal approval. Dev keys `dev-approver-key` and `dev-approver2-key` were added.
- `AgentHarness()` without an approval gateway now denies every approval (was: auto-approve).
  `CEAP_AUTO_APPROVE=true` outside `CEAP_ENV=dev` refuses to serve.
- `HarnessResult.warnings` is now `list[ResultWarning]` (code, message, source, severity), not
  `list[str]`; use `result.warning_messages()` for the old shape. The API status field now reports
  `COMPLETED`, `DEGRADED`, `INCOMPLETE_ANALYSIS`, `FAILED` or `CANCELLED`, and `to_dict()` adds
  `status`, `banner` and `incomplete_agents`.
- MCP trust fails closed: a missing `readOnlyHint` means not read-only and a missing or unknown
  `riskLevel` means HIGH (in-process adapter and stdio client). Tools of a server outside
  `CEAP_MCP_TRUSTED_SERVERS` (default: the eight built-in servers) cannot write. A tool result
  whose `schema_version` differs from `SCHEMA_VERSION` is rejected.
- Standalone stdio MCP servers enforce `required_capabilities` for the principal named by
  `CEAP_MCP_PRINCIPAL_ROLES`; unset means no capabilities, so `stage_orders` is refused.

#### Added

- Degradation ladder, visible on every result: rung 1 refusal answered by the pinned fallback
  model, rung 2 one structured-output repair retry, rung 3 canonical plan / deterministic
  critique / templated narrative, rung 4 `CEAP_LLM_DISABLED=true` kill switch with a banner. Each
  rung above 0 adds a `ResultWarning` and sets `DEGRADED`; `stop_reason` `max_tokens` and
  `refusal` are recorded with `stop_details`.
- Required specialists: the harness appends market, execution, quant, risk or engineering if an
  investigation plan omits one. A failed required agent yields `INCOMPLETE_ANALYSIS` and a report
  that says so instead of "no material anomaly"; a timed-out agent returns its checkpointed
  `PARTIAL` output.
- Model calls: per-call timeout `CEAP_LLM_TIMEOUT_S` (60 s), raced against cancellation,
  concurrency cap `CEAP_LLM_MAX_CONCURRENCY` (4) and per-task cap `CEAP_LLM_MAX_CALLS_PER_TASK`
  (12). The client retries 408/409/429/5xx and transport faults, never other 4xx. Adaptive
  thinking and the pinned `effort` are sent on the wire.
- Dockerfile: Python 3.12, digest-pinned base, dependencies (with `[llm]`) only from the hashed
  lock, non-root user, `HEALTHCHECK`; new `.dockerignore`.
- README banner (synthetic data, nothing trades, paper orders only, no live model numbers);
  threat model T17 corrected and T20-T22 added; 39 tests named `test_1_N_*` in
  `tests/agent/test_r1_fail_closed.py`.

#### Changed

- The job-prep word is gone from every tracked file; LEARN section 17 is now "Knowledge check".
  `Claude outputs/ci.yml` (a stale copy of the workflow) is deleted.
- CI run id for this round: 42396076903 (combined head e915c8a).

### Integration (combined 0.4.0 PR: R0 + R1 + R2)

- R2 shipped in 0.4.0 rather than 0.5.0 at the owner's request for one combined PR; version labels
  in code and docs say 0.4.0.
- `reporter` and `critic` now test `ResearchFlag.OOS_DECAY` (they still compared the literal
  `"OVERFIT"`, so those branches had stopped firing after the rename).
- README and LEARN research sections rewritten for the 30-scenario, t+1 re-baseline and the
  measured hurdle; 0.3.x live-model numbers labelled as belonging to the old 21-scenario set.

### R2 – signal-research quant correctness (0.4.0)

Implements items 2.1–2.14 of `docs/review/2026-11-07/PLAN.md`, in the binding order
2.1 → 2.9 → 2.10 → hurdle → 2.11.

- **2.1 Execution at t+1.** `execution_lag_days` (default 1) and `fill` (`close` | `open`) in the
  backtest, scope and MCP tool. Legacy R03 reversal: net Sharpe 1.73 at lag 0, −0.61 at lag 1
  (review: 1.80 → −0.67).
- **2.2 Weight drift** between fills; turnover from drifted to target weights.
- **2.3 Returns and financing.** Sharpe/Sortino on excess returns; cash = 1 − net earns rf (short
  rebate), negative cash pays rf + 50 bps; borrow 0.5/2/10 % by tier; 2x Reg T cap; Reg SHO SSR
  blocks logged; cost breakdown (spread, impact, borrow, financing). The Ken French rf CSV is
  **not committed** (a file download needs the owner's explicit OK); `scripts/fetch_rf.py` builds
  it; until then rf = 0, stated in every backtest's `assumptions`.
- **2.4 NYSE calendar** 2000–2030 (`ceap.data.calendar`, committed `data/reference/nyse_calendar.csv`
  checked against the rules), early closes, special closures, aware datetimes only.
- **2.5 Data policy.** ADV lagged one day; merger/performance delisting bounds both reported;
  explicit NaN policy; per-name weight and ADV caps inside the loop, logged.
- **2.6 Capacity sweep** 10–500 M with breakeven AUM and half-Sharpe AUM.
- **2.7 Statistics.** Gate on non-overlapping holding-period IC after the lag; Newey-West t;
  `ic_ir_annualised`; `turnover` renamed `rank_autocorrelation_turnover`; in-sample tail trimmed.
  Null |t| > 2 rate 6.0 % (500 seeds × 6 families).
- **2.8 Trial ledger and multiple testing.** Enumerated 24-configuration search space; append-only
  server-side ledger; deflated Sharpe from ledger trials; `OVERFIT` → `OOS_DECAY`, new
  `REGIME_BREAK`; locked `in_sample_end`; `HypothesisTable` on every assessment.
- **2.9 Generator** `realistic_v1`: 20 years, GARCH + fat tails, latent drift/transient premia,
  crash, volatility-dependent liquidity, listings/delistings; parameter-derived ground truth
  (`ceap.data.ground_truth`); power curve `docs/quant_power_curve.json`.
- **2.10 Null sweep and hurdle.** `ceap evaluate --suite null`. 500 seeds × 24 configurations:
  hurdle **t = 0.40** (15/500 false PROMOTEs, 3.0 %, band [1.7 %, 4.9 %]), sweep hash
  `ef474869e5fa503d593b460c7e8921a7d26f106d483a2c5c6c2708dc1faf57d4`, in
  `data/reference/research_thresholds.json`.
- **2.12 Risk.** Beta vs market ex-self with Vasicek shrinkage; factor, short-squeeze and
  liquidation stress; limits over the out-of-sample history with the worst date.
- **2.13 Membership mask** honoured by signals, IC, backtest and risk.
- **2.14 Profitability table** (`ceap profitability`), attached to every backtest result.

#### Breaking / migration

- `ResearchFlag.OVERFIT` is an alias of `OOS_DECAY` (value `"OOS_DECAY"`); stored `"OVERFIT"` is
  mapped on read. `PeriodSignalStats.turnover` is now a read-only alias of
  `rank_autocorrelation_turnover`.
- Backtests default to a t+1 close fill, drift, financing and caps; `run_backtest` refuses
  rebalance/weighting choices outside the search space via the MCP tool.
- Datasets are 5040 days with NaN prices outside membership; research catalogue is 30 scenarios.

#### 2.11 re-baseline: ground truth before and after (decision D5: flips stay REJECT)

| Scenario | Template | 0.4 truth | 0.5 truth (from parameters, t+1) | Change |
|---|---|---|---|---|
| RS01–03 | momentum_premium | PROMOTE | PROMOTE | same |
| RS04–06 | no_alpha | REJECT · NO_ALPHA | REJECT · NO_ALPHA | same |
| RS07 | reversal_premium | PROMOTE | REJECT · COST_DRAG | **flip** |
| RS08 | reversal_premium | PROMOTE | REJECT · COST_DRAG | **flip** |
| RS09 | reversal_premium | PROMOTE | REJECT · COST_DRAG | **flip** |
| RS10–12 | regime_break | REJECT · OVERFIT | REJECT · OOS_DECAY | flag renamed; premium now vanishes after the split |
| RS13–15 | cost_drag | REJECT · COST_DRAG | REJECT · COST_DRAG | same |
| RS16–18 | concentration | REJECT · CONCENTRATION, LIMIT_BREACH | same | same |
| RS19 | mixed | PROMOTE | REJECT · COST_DRAG (near) | **flip** |
| RS20 | mixed | PROMOTE | REJECT · COST_DRAG (near) | **flip** |
| RS21 | mixed | PROMOTE | REJECT · COST_DRAG (near) | **flip** |
| RS22–24 | momentum_near_threshold | – | PROMOTE (near) | new |
| RS25–27 | reversal_weak | – | REJECT · COST_DRAG | new |
| RS28–30 | momentum_crash | – | PROMOTE | new |

Results (mock model): 29/30 verdicts, clear 23/24, near-threshold 6/6, flags 0.90.

## 0.3.5 – Two new dedicated pages: AI/agents and quant

0.3.4 folded Stage 2 and the real-model findings into the *existing* docs but added no new
standalone page, so there was nowhere a reader could go to see "how does LLM/AI/agents work
here" or "how does quant work here" as its own answer. Added:

- `docs/ai-agents/ai-agents.md` – the LLM/AI/ML/agent boundary, where the model is actually
  called (and where it explicitly is not), the full agent roster across both pipelines, how the
  harness owns governance rather than the plan, the critic and narrative audits, the honest
  real-model findings from 0.3.1, and an explicit "what is not here" section (no trained ML
  models despite the `ml` extra, no autonomous trading loop, no fine-tuning/memory).
- `docs/quant/quant.md` – the deterministic quant layer for both verticals (execution-quality TCA
  and Stage 2 signal research), the formulas and thresholds behind `attribute_causes` and
  `assess_research`, why the synthetic generators are built to share code with the analytics
  that grade them, and an explicit "what is not here" section (no optimiser, no commercial risk
  model, no live data/OMS, no ML).

Linked from `README.md`, `docs/INDEX.md`, `docs/GITHUB_PAGES.md` and the `docs/index.html`
navigation bar.

## 0.3.4 – Documentation pass: catch up every doc with Stage 2 and the real-model findings

No code changes. `README.md`, `LEARN.md`, `COOKBOOK.md`, `docs/architecture/overview.md`,
`docs/threat-model/threat-model.md`, `docs/evaluation/evaluation.md`, `docs/DIAGRAMS.md`,
`docs/GITHUB_PAGES.md` and `docs/index.html` had drifted since v0.3.0-0.3.3 shipped Stage 2 and
the real-model bug fixes. Notably:

- The threat model's scope statement claimed the platform "does not place, amend or cancel
  orders" - no longer true since Stage 2's `execution.stage_orders`. Corrected, with three new
  threats (T17-T19) covering the order-staging trust boundary and its tests.
- The architecture overview, MCP topology diagram and `LEARN.md`'s tool table were still
  describing 5 servers / 26 tools; the platform now has 8 servers / 37 tools, and the agent
  roster tables only listed the 6 investigation agents, missing the 4 research agents.
- `LEARN.md` and `COOKBOOK.md` had zero mentions of Stage 2; added a guided-tour section and six
  cookbook recipes (research CLI/Python/API, order staging, adding a signal, adding a research
  scenario) at the same depth as the existing execution-quality content.
- `docs/index.html`'s measured-numbers block was still quoting 138 tests (now 212) and had no
  research-suite numbers; added them, plus a CI badge on `README.md` now that CI actually passes.
- Evaluation and cookbook docs now carry the honest real-model findings from 0.3.1 (planner
  model matters, two bugs the mock had hidden) instead of only the mock LLM's numbers.

## 0.3.3 – Fix CI for real: `pip-tools` has no cross-platform lock mode

0.3.2's fix (dropping the self-referential lockfile line) was necessary but not sufficient - the
next CI run failed on `pywin32==312`, a Windows-only transitive dependency of `mcp` with no
distribution for Linux. Root cause: `pip-tools` resolves `requirements.lock` for whatever
interpreter and OS it runs on (there is no `--universal`/cross-platform mode as in some other
resolvers), so regenerating it on Windows always reintroduces both the self-referential entry and
`pywin32` with no platform marker. Removed `pywin32` and added a comment at the top of
`requirements.lock` documenting both entries to strip by hand after every future regeneration.
CI verified green end-to-end after this fix (not just locally - see the process note below).

## 0.3.2 – Fix CI: drop the self-referential lockfile entry (incomplete)

CI has been failing since `requirements.lock` was introduced in 0.2.1 (never verified after
release - see the process note below). `pip-compile` recorded the project itself as a
`file:///C:/Work/Claude/...` requirement - a Windows absolute path that cannot resolve on the
Linux runner (`pip install -r requirements.lock` failed with `No such file or directory:
'/C:/Work/...'`). CI's second install step (`pip install -e . --no-deps`) already installs the
project itself, so the self-referential line in the lockfile was both wrong and redundant;
removed it. This alone did not fix CI - see 0.3.3.

Also (unreleased, from local testing): tried deriving pairwise relative-percent-change values for
the narrative number audit to reduce false positives on legitimate arithmetic; reverted after it
let a genuinely fabricated number ("99.9%") pass the audit undetected in the adversarial suite -
the fix made the audit's core fabrication-detection guarantee worse than the noise it removed.

**Process note:** none of v0.2.1, v0.3.0 or v0.3.1's CI runs were checked after release - all
three failed, unnoticed, until 0.3.2/0.3.3. Going forward, a release is not done until the actual
GitHub Actions run for that push is confirmed green, not just the local test suite.

## 0.3.1 – Real-model fixes found by a live Stage 2 evaluation run

Found by running `ceap evaluate --suite research` against `claude-haiku-4-5` for the first time
(prior verification used only the offline deterministic mock).

- `AnthropicLLMClient` sent `temperature`, which `anthropic` >= 1.x no longer accepts on
  `messages.create()`; every "live" call raised and silently fell back to the mock. Removed the
  parameter (sampling controls are gone from the current API; `LLMRequest.temperature` is
  unused).
- The narrative evidence-id audit flagged the approval request id (`APR-...`) as a fabricated
  evidence reference — it was never meant to resolve as `Evidence`. The audit regex now only
  matches ids shaped like an actual evidence prefix (`ceap.domain.evidence._PREFIX`).
- The narrative number audit didn't recognise bps/per-mille scalings a reporter model legitimately
  uses (e.g. quoting a fraction as bps); extended the derived-value set with `x1000`, `x10000` and
  their inverses.
- A real (non-mock) planner occasionally produced an incomplete research plan under
  `CEAP_LLM_MODEL=claude-haiku-4-5` alone (5/21 scenarios landed on `INCOMPLETE_ANALYSIS`).
  Setting `CEAP_LLM_PLANNING_MODEL=claude-sonnet-5` fixed it: 21/21 verdict accuracy and flag
  coverage on a second live run, with false-flag rate down from 0.33 to 0.10.

## 0.3.0 – Stage 2: Research → Alpha → Backtest → Risk → Critic → Human Approval

The platform now runs a second workflow on the same harness, policy engine, evidence model and
approval gateway: evaluate a trading signal on a research universe and propose whether to promote it,
with a human approval gate and an optional, separately approved paper order-staging step.

### Data and analytics
- `ceap.data.historical`: a seeded 30-name, four-year daily history generator (factor model with a
  scenario-controlled embedded premium computed from the same signal code the analytics use);
  `HistoricalStore` mirrors `DatasetStore`. Liquidity tiers, spreads and ADV are per name.
- `ceap.data.research_scenarios`: seven templates × three seeds = 21 scenarios with structured
  ground truth (`expected_verdict`, `expected_flags`): momentum premium, no alpha, reversal premium,
  regime break, cost drag, concentration, mixed.
- `ceap.analytics.signals` (momentum_12_1, reversal_5, low_vol_60), `signal_statistics` (rank IC,
  t-stat, IR, hit rate, turnover, decay, quantile spread - one-day ICs for honest t-stats),
  `backtest` (event-driven daily loop, half-spread + square-root impact cost model, walk-forward
  split, per-period statistics, P&L concentration), `portfolio_risk` (recomputed target portfolio,
  limits, stress) and `research_assessment` (the deterministic verdict and flags).
- `ceap.domain.research`: scope, statistics, backtest, risk and assessment objects plus
  `research_tool_arguments` - the single source of tool arguments for the plan, the agents and the
  harness tail, so no tool is invoked twice. `EvidenceType.SIGNAL` / `BACKTEST`.

### MCP servers
- New `research_data`, `alpha` and `backtest` servers; `risk` gains `get_portfolio_exposure`,
  `check_portfolio_limits`, `calculate_portfolio_stress` (they recompute the portfolio from
  `(signal, dataset, as_of)` and never trust backtest output); `execution` gains the only
  non-read-only tool, `stage_orders` (HIGH risk, requires `trading:execute`; idempotent paper
  orders held in memory) and `get_staged_orders`. All run in-process or over stdio.

### Harness, policy, agents
- `Task.input["kind"]` selects the workflow. The governance tail is harness-owned for both kinds
  and, for research, is critic → validation → **proposal approval** → [**staging approval** →
  `stage_orders`] → finalise; plan-supplied approval steps are stripped, a declined proposal fails
  the task, a declined staging approval skips staging and the report says so. The approver sees the
  critic's assessment and the recomputed target portfolio (or the order preview).
- Plan validation pins `signal`, accepts any symbol of the task universe, and pins `in_sample_end` /
  `as_of` to the task's dates. The planner only ever shows the model read-only tools.
- Agents `research`, `alpha`, `backtest`, `portfolio_risk`; the critic computes the deterministic
  research assessment and caps findings that contradict it; the reporter writes the proposal
  (HYPOTHESIS, SIGNAL STATISTICS, BACKTEST, RISK, VERDICT, APPROVAL, STAGED ORDERS) with the same
  number and evidence-id audits; `InvestigationReport.kind` / `proposal`.
- Capabilities: `research` (quant, admin), `research:read` (all roles), `trading:execute` (admin).
  Requesting order staging without `tools:write` + `trading:execute` is refused up front.

### API, CLI, evaluation
- `POST /research`, `GET /research[/{id}[/report|/result|/trace]]`, `POST /research/{id}/cancel`,
  `GET /research-scenarios`; `ReportOut.kind` / `proposal`; approvals endpoints unchanged.
- `ceap research "…" [--signal --dataset --stage-orders --role admin]`, `ceap research-scenarios`,
  `ceap evaluate --suite investigation|research|all`.
- 21-scenario research evaluation (verdict accuracy, flag coverage, false flags, approvals) next to
  the 50-scenario execution evaluation; new unit, MCP, agent, adversarial and API suites.

## 0.2.1 – CI, dependency pinning, test organisation

- Add GitHub Actions CI (`ruff`, `mypy`, `pytest`) running against a pinned `requirements.lock`.
- Pin `mcp>=1.19,<2` (the code uses the v1 `FastMCP` API, renamed in `mcp` 2.x) and generate
  `requirements.lock` via pip-compile for reproducible installs.
- `Settings.validate_for_serving()` now logs a warning when serving with dev API keys /
  auto-approve; `CEAP_API_KEYS` rejects duplicate key entries; add `.env.production.example`.
- Fix 13 latent `mypy` errors in the analytics layer surfaced by a newer `numpy` (widen
  `Sequence[float]` parameters to also accept `np.ndarray`; narrow `_round_tick`'s return type).
- Split `tests/unit/test_analytics.py` into per-metric files under `tests/unit/analytics/` with a
  few added edge-case tests (145 tests total; ruff and mypy clean).

## 0.2.0 – Review pass: hardening, correctness, bounded resources

Findings from a full code review (three independent reviewers plus `mypy --strict`-style typing),
each fixed with a regression test in `tests/agent/test_review_regressions.py`.

### Harness and governance
- Governance steps (critic → validation → finalise) are always appended in canonical order; a plan
  that places the critic before the specialists, or omits validation, is corrected and the change
  is recorded as a warning. A critic is forced before finalisation whenever agents ran after it.
- Plan validation pins `symbol` and `dataset` to the task and only accepts window arguments equal
  to the task boundaries; caps of 64 steps, 48 tool calls and 8 KB per argument set; only scalar,
  JSON-serialisable arguments are accepted. Policy-engine exceptions surface as plan errors.
- Cancellation during a concurrent tool batch now yields `CANCELLED` rather than `FAILED`
  (`TaskCancelled` unwrapped from `BaseExceptionGroup`). Per-attempt timeouts are bounded by the
  remaining task deadline; approvals race against cancellation; a crashing policy engine denies.
- Concurrent `REQUIRE_APPROVAL` steps use a counter so the state machine does not flap.
- `TransientToolError` (connection/OS/timeout errors from MCP transports) is the only retryable class.

### Agents and LLM
- Planner sanitises arguments against the tool schema (drops unknown keys, wrong types and non-
  scalars; re-pins symbol/dataset) and reports `rejected_steps`; planning facts are passed as
  request metadata so `{}` in the user question can no longer hijack the mock planner.
- Agents only reuse a cached tool output when the arguments match exactly.
- Critic tolerates an LLM failure; NaN confidence proposals are ignored; reporter, critic and
  planner surface `llm_fallback` and the router records `fallback_reason`.
- Execution/engineering agents format `None`/non-finite values safely; deployment timestamps are
  compared as datetimes rather than ISO strings.
- Prompts frame retrieved documents and tool outputs as data, not instructions.

### Analytics and MCP servers
- `venue_volume` sums per venue (previously kept only the last print).
- Post-trade impact is measured at last fill + horizon; opportunity cost pro-rated for sub-horizon
  windows; quote index returns `None` before the first quote; mids use `[start, end)`.
- Attribution is `None`/NaN safe; deterioration uses only finite deltas; peer means ignore NaN.
- `downsample` keeps the first and last points; `get_order_book` returns the book at the exact
  requested timestamp; `check_limit` accepts `side` and uses the current position.
- Optional parameters export as `anyOf` with `null` in MCP schemas; stdio servers run with the
  current interpreter and forward `PYTHONPATH`/virtualenv variables; session access is serialised.

### API, configuration and observability
- Development API keys and auto-approval apply only when `CEAP_ENV=dev`; `validate_for_serving()`
  refuses to start otherwise. Numeric/boolean settings are validated; keys must be ≥ 8 characters.
- Background investigations run under a semaphore (`CEAP_MAX_CONCURRENT_INVESTIGATIONS`), record
  failures instead of vanishing, and are cancelled on shutdown; retained results are bounded
  (`CEAP_MAX_RETAINED_RESULTS`); cancel requires the owner or `approvals:decide`.
- Constant-time API-key lookup; principals are logged as a key digest. Approving a decided request
  is a 409. Malformed `session_date` is a 422.
- NaN/Infinity are scrubbed at the JSON boundary (`to_jsonable`, CLI `allow_nan=False`).
- Metrics registry bounded to 500 label sets per metric with escaped labels; tracer uses a
  contextvar (correct parents under concurrency) and caps retained spans; mock LLM call log and
  approval logs are bounded.
- Question parser: first-mentioned symbol wins; clock-like ranges beat numeric ranges with units.
- Dependencies: `mcp>=1.19`, `tzdata` for Windows time zones.
- 138 tests (22 new regressions); ruff and mypy clean; evaluation unchanged at 50/50.

## 0.1.0 – MVP: Execution Quality Investigation

- Domain model (tasks, plans, tools, evidence, findings, market/execution objects, policy).
- Deterministic analytics: VWAP, TWAP, implementation shortfall, slippage, spreads, market impact,
  liquidity, volatility, market statistics, TCA bundle with venue statistics, cause attribution.
- Synthetic cash-equity market model with 10 scenario templates x 5 symbols (50 scenarios) and
  structured ground truth.
- Five MCP servers (market data, execution, risk, engineering, knowledge) usable in-process or over
  stdio via FastMCP; MCP client abstraction, tool adapter with evidence digests, registry discovery.
- Agent harness: explicit state machine, policy-gated executor with approvals, retry, timeout,
  cancellation, memory, tracing, concurrent tool batches, enforced governance steps.
- Agents: planner (LLM, sanitised), market, execution, quant, risk, engineering, critic
  (deterministic + LLM that can only lower confidence), reporter with narrative number/evidence audits.
- Policy engine with RBAC, argument guards, symbol universe, risk-level approvals.
- RAG knowledge base over runbooks/policies exposed as an MCP server.
- FastAPI gateway with API-key auth and capability checks; CLI; Prometheus metrics; JSON logging;
  OpenTelemetry-ready tracing.
- 116 tests: unit, integration, MCP (incl. real stdio round-trip), agent, adversarial, 50-scenario evaluation.
