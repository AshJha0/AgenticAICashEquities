# AI-2: agents, harness, planning, state machine, MCP, durable runs, policy and approvals

Reviewer AI-2, CEAP v0.9.0 (tag `dd6bf3c`), 2026-11-12. Read-only review. Three small probes were
run with the main checkout's venv and `PYTHONPATH=src` on this worktree (mock LLM, in-memory DB, no
Ollama, no files left behind); each is named in the finding it supports.

## 1. Scope covered, and what I did not get to

Read in full: `harness/engine.py`, `executor.py`, `planning.py`, `dispatch.py`, `approvals.py`,
`state_machine.py`, `durable.py`, `replay.py`, `memory.py`, `retry.py`, `cancellation.py`,
`common.py` (most), `evidence.py` (most); `agents/planner.py`, `agents/base.py`;
`policy/engine.py`, `policy/approvals.py`, `policy/agents.py`, `policy/permissions.py`;
`mcp/server.py`, `client.py`, `adapter.py`, `registry.py`, `cpu_pool.py`, `mcp/execution/server.py`
(staging tool); `domain/plans.py`, `domain/tasks.py`; `platform.py` (class `Platform`);
`api/routes.py` (create, approvals), `api/auth.py` (principal naming); ADR 0002 and 0004;
`docs/RUNBOOK.md` "Restart"; `tests/unit/test_r090_durable.py`, `test_r090_chaos.py` (G5 part),
`tests/properties/test_harness_invariants.py`, the replay test in `tests/integration/test_r4_platform.py`;
`eval/agents.py` planner scorer and `eval/gold_plans.json`.

Not covered: `agents/reporter.py` (1,072 lines) and the domain and research agents (not read);
`harness/paper_gate.py`; the seven MCP servers other than `execution` (only grepped);
`mcp/execution/staging.py` beyond its signatures; `api/loopmon.py` (approval sweep);
`tests/contract`, `tests/adversarial`, `tests/mcp` beyond test names; the full suite was not run;
I did not check online whether an `mcp` 2.x SDK exists (no network use), so the pin assessment
in section 6 is conditional.

## 2. Score: 6 / 10 against a top-firm bar

- The control side is strong and I could not break it through the API: policy before every
  harness tool call, fail-closed on rule errors, HIGH risk always needs a second person,
  authenticated principal names that cannot collide with system actors, content-hash binding.
- The "durable runs" feature is the weakest part of the area: the journal exists and works, but
  there is no way to resume a task through the product, the approval half does not resume in
  practice (probe), and the tests prove a narrower thing than the docs claim.
- The standalone MCP server is a real path around the policy engine for the one mutating tool.
- "Agentic" is a generous word for what runs: a fixed governed pipeline in which the model picks
  optional extra reads and two enumerated scope choices. `ai-agents.md` says this honestly; the
  planner metrics published on the README do not help a reader see it.
- State machine and plan are typed but the lifecycle table does not carry the governance
  invariants; they live in local variables of one 260-line method.

## 3. What is good (verified)

- Policy is evaluated before every harness tool call and a crashing rule denies
  (`harness/executor.py:115-131`); plan validation pre-checks every tool step and fails closed
  (`harness/planning.py:110-119`).
- HIGH risk is `REQUIRE_APPROVAL` whatever the capability; CRITICAL is never invocable
  (`policy/engine.py:322-334`). The one exemption is a single-use grant bound to task, tool and
  argument hash (`policy/engine.py:87-109`).
- Approver identity comes from the API key, never from the request body
  (`api/routes.py:650-657`, `api/auth.py:221-224`); names are `role:<id>` or `user:<subject>`, so
  nobody can be called `policy` or `auto-approver` and skip four-eyes (`policy/approvals.py:75`, `:87`).
- A decision is a conditional PENDING transition, so of two decides one wins and the late one is
  audited (`policy/approvals.py:352-359`); the hash is re-checked after the decision (`:123-131`).
- Default gateway denies (`harness/engine.py:124`); MCP annotations fail closed: missing
  `readOnlyHint` means mutating, missing `riskLevel` means HIGH (`mcp/adapter.py:92-97`, `:111-112`);
  untrusted servers are denied even when they claim read-only (`policy/engine.py:137-152`).
- The planner never sees a mutating tool (`agents/planner.py:135`), scoping arguments are pinned to
  the task (`:398-403`), governance steps are stripped from any plan and re-appended by the harness
  (`harness/planning.py:164-213`). Agents run under a narrowed capability set with no
  `tools:write` (`policy/agents.py:64-74`).
- Cancellation races every tool await, model call and approval wait (`harness/executor.py:54-74`,
  `agents/base.py:81`); CPU tools in the API server are really killed on timeout, one slot per call
  (`mcp/cpu_pool.py:154-166`).
- Budgets are enforced, not advisory: plan size and tool-call caps (`harness/planning.py:79-88`),
  per-task model-call cap (`agents/base.py:71-77`), step and task timeouts (`harness/executor.py:194-199`).
- The harness tests assert invariants under adversarial fake clients with Hypothesis
  (`tests/properties/test_harness_invariants.py:60-104`); only one agent snapshot file exists.
- Replay is tested in a fresh process, and tampering is located
  (`tests/integration/test_r4_platform.py:431-454`).

## 4. Findings

### AI-2-01 [P1] Durable runs cannot be resumed through the product; only a test that forges a task id resumes
- Evidence: `src/ceap/domain/tasks.py:77` (`Task(id=new_id("TASK"), ...)`, every submit gets a new
  id); `src/ceap/platform.py:176-179` (a restart only marks tasks `INTERRUPTED`); no route or CLI
  command takes an existing task id (`src/ceap/api/routes.py:203-278`, `src/ceap/cli.py:251-332`);
  `tests/unit/test_r090_chaos.py:150` (`dataclasses.replace(p.build_task(req), id=id_file.read_text())`);
  claims at `docs/RUNBOOK.md:28-31`, `COOKBOOK.md:960-962`, `CHANGELOG.md:29`, `README.md:196`.
- Status: VERIFIED (read the code; grepped `src` for `resume`, `journal`, `INTERRUPTED`)
- Confidence: high
- Why it matters at a top firm: the docs say "re-running the same task id reads them back". An
  operator has no command that re-runs a task id, so after a crash the journal rows are never read
  and are later pruned by retention. A reader who tries the Cookbook recipe finds a feature flag
  that writes rows and nothing that consumes them. This is a claim that fails in five minutes.
- Fix: add `POST /investigations/{id}/resume` (and `/research/{id}/resume`, plus `ceap resume <id>`)
  that loads the stored task row, requires status `INTERRUPTED`, the same owner and the journal
  flag, and calls `_execute` with the original id. Acceptance test: start the API in a child
  process, kill it mid-run, restart, call resume through HTTP, assert zero repeated tool calls and
  a completed report. Until then, change the four doc lines to "journal only; resume entry point
  planned".

### AI-2-02 [P1] "Resumable approvals" do not resume: a re-run asks the human again and writes a second case
- Evidence: `src/ceap/harness/approvals.py:60-70` (key is the content hash of the payload);
  `src/ceap/harness/common.py:238-245` and `src/ceap/policy/approvals.py:421-431` (the payload holds
  `finding_id`, evidence ids and refs); `src/ceap/domain/findings.py:70` (`id=new_id("F")`, fresh on
  every run); claim at `src/ceap/harness/durable.py:18-20` and `CHANGELOG.md:305`.
- Status: VERIFIED (probe: investigation with `signoff`, mock LLM, same `Task` and one
  `InMemoryRunJournal`, run twice. Run 2 replayed 37 tool calls but the gateway was asked again:
  hashes `0463642f291c` then `8155d90aa19b`; zero `approval.resumed` events; case memory size 2.)
- Confidence: high
- Why it matters at a top firm: the stated guarantee is that a restart "does not ask the approver
  twice for the same content". In practice the content never hashes the same, because agent
  findings and calculation evidence get new random ids on each run. The only tests are of the key
  function and of `SqlApprovalStore.decided_for`, which no source file calls
  (`src/ceap/persistence/repositories.py:208`). The harness path at `approvals.py:71-78` and
  `executor.py:256-263` has no test.
- Fix: hash a semantic approval payload (tool id, arguments, portfolio hash, verdict, hypothesis
  verdicts, finding statements and values) and keep ids out of the hash, or derive finding and
  evidence ids from content. Acceptance test: the probe above as a test, asserting one gateway
  request across two runs and one case record; then the same through a killed child process.

### AI-2-03 [P1] The standalone MCP execution server stages orders with no approval, no four-eyes and no kill switch
- Evidence: `src/ceap/mcp/server.py:213-217` (the exported tool checks capabilities only);
  `src/ceap/mcp/execution/server.py:190-223` (description says "Requires ... human approval";
  `approved_portfolio_hash` is optional and skipped when `None`, `:205-209`);
  `src/ceap/mcp/execution/__main__.py:8` (no database, so `halt_reason()` returns nothing,
  `src/ceap/mcp/execution/staging.py:159-161`); `docs/adr/0002-policy-engine-is-the-authority.md:13`
  ("Every tool call goes through the harness"); `docs/configuration.md:27`.
- Status: VERIFIED (probe: `build_server(...).to_fastmcp(capabilities_for(["admin"]))`, then
  `call_tool("stage_orders", {"signal": "momentum_12_1"})` returned `status: STAGED`, 18 orders,
  gross 50,000,000, with no approval step. With the `quant` role it is refused.)
- Confidence: high
- Why it matters at a top firm: ADR 0002 names the policy engine as the single authority. With
  `CEAP_MCP_PRINCIPAL_ROLES=admin`, any MCP client attached to `python -m ceap.mcp.execution`
  (a desktop assistant, for example) can stage a paper order set directly: no `REQUIRE_APPROVAL`,
  no second person, no symbol-universe or argument guards, no DB kill switch, no audit entry.
  The orders are paper and the role must be set by the operator, which is why this is P1 and not
  P0, but the server's own tool description promises an approval that this path never asks for.
- Fix: in `to_fastmcp`, refuse every tool with `risk_level` HIGH or `read_only=False` unless the
  server was built with an approval hook, and say so in the error; or run the same
  `RulePolicyEngine` inside `_jsonable_wrapper`. Make `approved_portfolio_hash` required for
  `stage_orders`. Correct ADR 0002 to state the standalone path. Acceptance test: the probe above
  must raise for every role.

### AI-2-04 [P2] Side-effecting tool calls are at-least-once; three retry layers ignore `read_only`
- Evidence: `src/ceap/harness/executor.py:235-237` (journal row written after the tool returns;
  no intent row before); `src/ceap/harness/retry.py:19` (`retry_on` includes `TimeoutError`) with
  `src/ceap/harness/executor.py:204-213`; `src/ceap/mcp/client.py:215-217` (the stdio client
  re-sends the call after a reconnect); worked example at `src/ceap/harness/durable.py:24-26`
  ("killed during `stage` ... runs `stage` once").
- Status: VERIFIED (read the code). The chaos test kills the process immediately after a journal
  write (`tests/unit/test_r090_chaos.py:122-123`), never between a side effect and its write.
- Confidence: high
- Why it matters at a top firm: "it must never repeat a side effect" (`durable.py:4`) is not what
  the journal gives. A crash, a timeout or a transport drop after the tool acted and before the
  result was recorded repeats the call. Today the only mutating tool is saved by its own
  idempotent `staging_id` in DB mode; the guarantee belongs to that tool, not to the harness, and
  the next mutating tool will not have it.
- Fix: pass the idempotency key to the tool (`ToolExecutionContext`) and require mutating tools to
  dedupe on it; write an `intent` journal row before a non-read-only call and, on resume, ask the
  tool for the outcome instead of calling again; do not retry non-read-only tools on timeout or
  reconnect. Acceptance test: a fake mutating tool with a counter, killed after the effect and
  before the journal write, ends with counter 1 after resume.

### AI-2-05 [P2] A resumed run with a real model re-plans; neither the plan nor agent results are journalled
- Evidence: `src/ceap/harness/engine.py:186` (the planner is always called);
  `src/ceap/harness/durable.py:41-42` (journal kinds are `tool` and `approval` only);
  `src/ceap/agents/planner.py:346` (step ids are positional in the model's reply).
- Status: VERIFIED (read the code); the effect with a live local model is JUDGEMENT (not run).
- Confidence: medium
- Why it matters at a top firm: the idempotency key includes the step id. A second planning call
  to a 7B model that is valid half the time will often return a different list, so model-chosen
  steps miss the journal and run again, and the resumed run can carry a different baseline choice
  than the one whose results were journalled. Both durable tests use the mock client, whose plan
  is fixed. Model calls are also repeated in full (the slowest and least repeatable part).
- Fix: journal the planner output (plan, scope) and each agent result under the task id, and read
  them back first. Acceptance test: a fake client that returns a different valid plan on its
  second call; the resumed run must use the first plan and make zero model calls for done steps.

### AI-2-06 [P2] Auto-approvals and timeouts are stored in case memory as human-decided cases
- Evidence: `src/ceap/harness/approvals.py:121-122` (no `SYSTEM_ACTORS` check before
  `_record_case`); `src/ceap/harness/evidence.py:260-275`; claim at `src/ceap/harness/memory.py:251`
  ("written only by the harness, after a human decision") and `agents/planner.py:456`
  ("PRIOR CASES (read-only context from human-decided cases ...)").
- Status: VERIFIED (probe: `auto_approve=True`, investigation with `signoff`: case memory holds
  `('APPROVED', 'auto-approver')`.)
- Confidence: high
- Why it matters at a top firm: the planner is told these are human precedents. A dev-mode
  auto-approval, or a `REJECTED` that is really a 120-second timeout, becomes a precedent with the
  same weight as a reviewed decision. That is memory poisoning by the system itself.
- Fix: return before `_record_case` when `decision.decided_by in SYSTEM_ACTORS`. Acceptance test:
  the probe, asserting an empty memory; and a timeout decision, asserting the same.

### AI-2-07 [P2] Case memory has no owner boundary, no persistence and no expiry
- Evidence: `src/ceap/harness/common.py:190-192` (partition is the dataset only);
  `src/ceap/harness/memory.py:232-242` (the view carries another task's question and the approver's
  free-text reason); `:260` ("The store is per process"); `:278` (a full partition silently
  refuses new cases); `src/ceap/agents/planner.py:454-460`.
- Status: VERIFIED (read the code)
- Confidence: high
- Why it matters at a top firm: the API enforces that a reader sees only its own tasks
  (`tasks:read-any`), but a trader's question text and an approver's comment are placed into the
  next principal's planner prompt for the same dataset. Content is redacted and wrapped in a data
  block, so this is a confidentiality and provenance gap more than an injection hole. Separately,
  "memory" that is empty after every restart and frozen after 1,000 cases is not yet a feature.
- Fix: partition by (dataset, owner or desk), store `decided_by` role and source task id in the
  record, persist in a table with the audit entry id, add a TTL and an eviction rule, and record in
  the result which case ids reached the prompt (already done) and whose they were. Acceptance
  test: user A's case is never returned for user B without `tasks:read-any`.

### AI-2-08 [P2] Time spent waiting for a human counts against the task deadline
- Evidence: `src/ceap/harness/engine.py:141` and `:214-215`; `src/ceap/harness/approvals.py:83`;
  `src/ceap/config.py:113-115` (300 s default) and `:140` (120 s per approval).
- Status: VERIFIED (read the code; not run)
- Confidence: high
- Why it matters at a top firm: a research task with two approvals can spend 240 of its 300
  seconds waiting for people, and a local 7B model can use the rest. An approval given after the
  deadline is followed at the next loop turn by `TimeoutError("task deadline exceeded")`: the task
  fails after a human approved it, the staging grant stays in `RulePolicyEngine._granted`
  (`src/ceap/policy/engine.py:73`), and the approver's work is lost. Real approval queues take
  minutes to hours.
- Fix: stop the task clock while in `AWAITING_APPROVAL` (extend the deadline by the wait), or give
  approvals their own budget and make the run resumable from the decision (see AI-2-01). Drop
  unused grants when the run ends. Acceptance test: injected clock, approval decided at
  deadline + 1 s, the staging step still runs.

### AI-2-09 [P2] A resumed approval is taken from an unauthenticated journal row, not from the approvals table or the audit chain
- Evidence: `src/ceap/harness/executor.py:255-262`, `src/ceap/harness/approvals.py:70-77`
  (`approved` and `decided_by` read from `run_journal`); `src/ceap/persistence/repositories.py:208`
  (`decided_for` on the approvals table exists and is never called);
  `src/ceap/harness/executor.py:250-299` (the tool-level path has no `check_four_eyes` after the
  gateway, unlike `harness/approvals.py:99-104`).
- Status: VERIFIED (read the code)
- Confidence: high
- Why it matters at a top firm: the approvals table has one-winner transitions and every decision
  is in the hash-chained audit log. The journal has neither: a row `{"approved": true}` is plain
  JSON, a reused decision writes only a trace event, and the reuse skips the gateway where
  four-eyes is checked for tool-level approvals. Anyone who can write the DB can do worse, so this
  is defence in depth, but an auditor will ask why two stores answer "was this approved".
- Fix: on resume call `SqlApprovalStore.decided_for(task_id, step_id, content_hash)`, re-run
  `check_four_eyes`, and append an `approval.reused` audit entry that names the original approval
  id. Add the harness-level four-eyes re-check to `_seek_approval`. Acceptance test: a forged
  journal row with no matching approvals row is ignored and the human is asked.

### AI-2-10 [P2] Replay proves the harness is deterministic against itself, not that it reproduces the live run or its prompts
- Evidence: `src/ceap/harness/replay.py:210-229` (model replies are served by purpose and order;
  the request is not compared); `:361` (`prompt_hashes` stored, never read back; only a test
  asserts they exist); `:363` (the live status is stored, never compared); `:313-318` (the replay
  platform has an empty case memory whatever the live run recalled).
- Status: VERIFIED (read the code; grepped for readers of `prompt_hashes` and `live`)
- Confidence: high
- Why it matters at a top firm: the stored result is produced by pass 2, so "byte-for-byte" is
  true by construction on the day of recording. A later code change that alters what is sent to
  the model (a broken data block, a leaked field, a different tool catalogue) replays equal as
  long as the downstream result is the same, and a live run that used prior cases is replayed
  without them. The module docstring is honest about the two passes; the gap is what is not checked.
- Fix: in `ReplayLLM.complete` compute the redacted request hash and compare with the recorded
  `prompt_hash` (report the first differing exchange); compare the replayed status with
  `bundle["live"]`; record the recalled cases in the bundle and seed the replay memory with them.
  Acceptance test: change one word of a prompt template and assert the replay reports a prompt
  difference, not a result difference.

### AI-2-11 [P2] The state machine records the lifecycle; it does not enforce the governance order
- Evidence: `src/ceap/harness/state_machine.py:36-42` (`EXECUTING -> FINALISING` is legal with no
  critique or evidence validation); `:44` (`CRITIQUING -> VALIDATING_EVIDENCE` is never used);
  `src/ceap/harness/engine.py:208-209`, `:350`, `:365` (the invariant is two local variables);
  `:234-391` (an `if / elif` chain over `StepType` with no `else`); `src/ceap/harness/dispatch.py:219`
  (a forced critic runs while the state stays `EXECUTING`).
- Status: VERIFIED (read the code) for the facts; JUDGEMENT for the severity
- Confidence: high
- Why it matters at a top firm: transitions are typed and checked at run time, and I found no
  reachable illegal transition. But the table would accept a run that finalises without a critic,
  so "explicit state machine" adds less assurance than the phrase suggests, and a seventh
  `StepType` would be skipped without a word. The property test that guards the order checks
  step ids in the plan, not the state history.
- Fix: remove `EXECUTING -> FINALISING` and require `VALIDATING_EVIDENCE -> FINALISING`; enter
  `CRITIQUING` in `_force_critic`; end the dispatch with `else: assert_never(step.type)`; add a
  property that every `COMPLETED` history contains `CRITIQUING` then `VALIDATING_EVIDENCE` then
  `FINALISING`. See section 6(a) for the thin graph description.

### AI-2-12 [P2] The published planner metrics say little about the plan that runs
- Evidence: `src/ceap/eval/agents.py:120-138` (precision = planned tools inside a 26-tool
  "allowed" list, which is every non-research read tool in `eval/gold_plans.json`; the output
  schema already admits only catalogue tools); `src/ceap/harness/planning.py:179-192` and
  `:304-327` (the harness appends all five required specialists and every data read they need);
  `README.md:19`, `docs/ai-agents/ai-agents.md:375` ("tool precision 1.00, recall 0.73").
- Status: VERIFIED (read the scorer and the gold file)
- Confidence: high
- Why it matters at a top firm: precision 1.00 is close to structural, and recall 0.73 is measured
  on the raw reply while the executed plan has recall 1.0 for required data whatever the model
  said. The honest number is "plan valid 15/30", and the question a senior reader asks next is not
  answered anywhere: when the model's plan is used instead of the canonical one, does the report
  change? Ten gold questions on one symbol and one dataset is also thin.
- Fix: publish (1) the share of executed tool steps that came from the model versus the harness,
  (2) argument accuracy and baseline-choice accuracy against gold, (3) a counterfactual: golden
  score with the model plan versus the canonical plan on the same items, with an interval. Report
  precision against the per-question gold set, not the global allowed list. See section 6(f).

### AI-2-13 [P2] MCP in the product path is an in-process tool registry; schemas carry types only
- Evidence: `src/ceap/platform.py:149` (default `build_in_process_client`); `StdioMCPClient` has no
  caller in `src` (`src/ceap/mcp/__init__.py:10` export only); `src/ceap/mcp/client.py:55-61`
  (`spec.fn(**arguments)`, no server-side validation); `src/ceap/mcp/server.py:75-89` (schema =
  type per parameter, no `additionalProperties: false`, no enum, no bounds, arrays without
  `items`); `src/ceap/mcp/server.py:7` (docstring offers "streamable HTTP"; only `run_stdio` exists, `:200-201`).
- Status: VERIFIED (read the code)
- Confidence: high
- Why it matters at a top firm: the architecture docs do say "in-process or stdio", so this is not
  dishonest, but a reader told "MCP servers per domain" should know that the API, the CLI and
  nearly every test never speak the protocol; three lifecycle tests and one round trip do. Input
  validation is the policy engine's schema rule over a types-only schema, so `gross_notional=-1`,
  `k=10**6` or a misspelt argument are caught late or by the tool, not by the contract.
- Fix: state the default transport in ADR 0004 and `ai-agents.md`; generate schemas from Pydantic
  models with bounds, enums and `additionalProperties: false`; run the contract suite once over
  stdio in CI (already close: `tests/contract/test_adapter_differential.py`); delete the HTTP claim
  or implement it with auth.

### AI-2-14 [P3] Property invariant 6 ("an approver always differs from the requester") is vacuous
- Evidence: `tests/properties/test_harness_invariants.py:93-94` reads `r.get("decided_by")`;
  `src/ceap/harness/engine.py:335-341` (approval step records hold only `step_id`, `type`, `status`).
- Status: VERIFIED (read both)
- Confidence: high
- Why it matters at a top firm: `None != req.principal` is always true, and investigations without
  `signoff` have no approval step at all, so the invariant can never fail.
- Fix: put `decided_by` in the step record (or read `agent_outputs`/scratch approvals), run the
  property with `signoff=True` and a gateway that tries the requester's own name.

### AI-2-15 [P3] Agents receive the mutable run memory, including approvals and the call counter
- Evidence: `src/ceap/harness/dispatch.py:110-115` (`"memory"`, `"scratch"` passed by reference);
  scratch holds `approvals`, `approved_portfolio_hash` (`harness/approvals.py:112`, `:133`) and
  `llm_calls` (`agents/base.py:72-77`).
- Status: VERIFIED (read the code)
- Confidence: high
- Why it matters at a top firm: agents are trusted in-process code today, so this is not a hole.
  It is the reason "agents never control the harness" (`engine.py:6`) is a convention and not a
  property: one agent can reset its own budget or set the approved portfolio hash.
- Fix: give agents a read-only view plus a narrow write API (`checkpoint`, `add_evidence`); keep
  approvals, grants and counters on the harness object.

### AI-2-16 [P3] Small harness defects
- Evidence: `src/ceap/harness/planning.py:369-376` (a re-plan's planner result is not passed to
  `_absorb`, so its degradations and scope are dropped); `src/ceap/harness/engine.py:178` and `:415`
  (`llm_scope.__enter__()` with the `__exit__` outside any `finally`; an `asyncio.CancelledError`
  skips it); `src/ceap/persistence/repositories.py:235-250` with `src/ceap/persistence/db.py:209`
  (each journal write blocks the event loop on the writer thread and stores the full tool payload);
  `src/ceap/api/routes.py:656` with `src/ceap/policy/approvals.py:338` (the reviewed content hash is
  optional on a decision).
- Status: VERIFIED (read the code)
- Confidence: medium
- Why it matters at a top firm: each is minor; together they are the kind of loose end a code
  reader finds in the first hour.
- Fix: absorb the re-plan result; wrap the scope in `with`; write the journal with `db.arun`;
  require `content_hash` on `POST /approvals/{id}` outside dev. One test each.

## 5. Plan of 2026-11-09: items in my area

| Item | State | Note |
|---|---|---|
| A3 schema and symbol validation in policy | done and convincing | `policy/engine.py:193-286`; the schemas themselves are thin (AI-2-13) |
| A4 HIGH risk always needs approval | done and convincing in the harness | `policy/engine.py:326-334`; not on the standalone server (AI-2-03) |
| A5 untrusted servers denied, catalogue hash | done; pinning is opt-in | `policy/engine.py:137-152`, `mcp/registry.py:99-113` |
| A11 planner sanitiser | done and convincing | `agents/planner.py:363-431` |
| A14 approver sees arguments and hash; re-check | done but weak | hash is optional on decide (AI-2-16) |
| A16 per-agent least privilege | done and convincing | `policy/agents.py:40-74`; memory is still shared (AI-2-15) |
| B2 per-agent evals | done but weak | planner metrics (AI-2-12) |
| B9 docs say which agents use the model | done and convincing | `docs/ai-agents/ai-agents.md:176-188` |
| G5 durable runs, resume, resumable approvals, idempotency key | done but weak | journal yes; resume entry point no (AI-2-01); approvals do not resume (AI-2-02); at-least-once (AI-2-04) |
| G8 approval TTL sweep | not reviewed | `api/loopmon.py` not read |
| H6 split the engine god-module | done but weak | mixins over a shared `self`; `execute` is still one 260-line method with the invariants in locals (AI-2-11) |
| I1 ADRs 0002 and 0004 | done but weak | 0002 overstates ("every tool call"); 0004 does not say the default is in-process |

## 6. Candidates: present now, value, how to measure, cost, verdict

Direct answers first.

**Is it agentic?** It is a fixed, governed pipeline with three bounded model steps. For an
investigation the model decides: which optional read-only tools to add; one baseline from an
enumerated list (this does change numbers); a ranking of pre-registered hypotheses; in the critic,
which of a few named checks to run (at most three tool calls, `harness/evidence.py:116-131`); in
the reporter, which claims to word. The harness decides everything else: the five specialists
always run (`harness/common.py:32`), their data reads are scheduled by the harness
(`harness/planning.py:304-327`), the tail is fixed, there is one re-plan and it only re-fetches
missing reads (`harness/planning.py:377-385`; the model's new agent steps and scope are ignored),
and the model never decides to stop. When the reply is invalid after one repair the plan is
sanitised step by step, and if no valid tool call is left the canonical template is used
(`agents/planner.py:231-263`); either case is marked `DEGRADED`. With 15/30 valid, about half of
local-model runs execute a repaired or template plan, and in all runs the required reads come from
the harness. `ai-agents.md` says the first part; nothing says the second with a number.

**(a) Explicit typed agent graph with checkpoints, conditional edges and interrupts**
- Present now: typed lifecycle table (`harness/state_machine.py:32-50`); the real graph is implicit
  in `engine.py:212-391`; checkpoints are the tool journal only; an "interrupt" is an awaited
  future inside a live coroutine (`policy/approvals.py:292`), so it dies with the process.
- Value here: a LangGraph-style design would give three things CEAP lacks: state persisted at
  every node (so plan and agent results resume, AI-2-05), interrupts that survive a restart and
  stop the clock (AI-2-01, -02, -08), and a graph that can be printed and checked. It would lose,
  or have to rebuild, what is already better here than framework defaults: policy before every
  call, single-use grants, four-eyes, hash binding, byte-level replay under an injected clock.
  A rewrite onto a framework is not justified, and a new heavy dependency cuts against the
  hashed-lock discipline.
- Do instead: a thin graph description over the existing engine: a `NODES`/`EDGES` table with a
  guard per edge (for example `finalise` requires `critic_ran and evidence_validated`), `execute`
  driven by that table, one checkpoint row per node exit (kind `node`, serialised memory), and
  approval as a persisted interrupt with a resume route.
- Measure: kill-at-every-node test (for each of N node boundaries: kill, resume, assert zero
  repeated tool and model calls and an equal normalised report); count of invariants enforced by
  table versus by local variable (today 0 of 3).
- Cost: 6 to 8 plan-days, no compute, no licence.
- Verdict: adopt now in the thin form; not the framework.

**(b) Agent memory with provenance and safety limits**
- Present now: `harness/memory.py:248-315`: harness-written, append-only, audited on write,
  partitioned by dataset, 1,000-case cap, BM25 lookup, off in evaluation, views of 300 characters
  inside data blocks. Missing: human-only filter (AI-2-06), owner boundary, persistence, expiry
  (AI-2-07), and any measurement that it helps.
- Value here: unproven. It is disabled in every evaluation, so there is no evidence that prior
  cases improve a plan, and with enumerated planner choices the upside is small.
- Measure: golden-set A/B with memory seeded from the other items' signed decisions (leave one
  out), same seeds, report the difference in primary-cause accuracy and plan-valid rate with an
  interval; plus a poisoning test (a seeded wrong precedent must not flip a deterministic verdict).
- Cost: fixes 2 plan-days; A/B about 1 plan-day plus one local evaluation run (hours of laptop CPU).
- Verdict: fix AI-2-06 and -07 now; keep the feature off by default and do the A/B later; remove
  it if the A/B shows nothing.

**(c) MCP beyond tools: resources, prompts, sampling, structured output, elicitation**
- Present now: tools only (`tools/list`, `tools/call`), stdio only, annotations plus `_meta` for
  risk (`mcp/server.py:186-197`); the client reads `structuredContent` when present
  (`mcp/client.py:247-252`) but servers declare no output schema of their own; no resources,
  prompts, sampling, elicitation, roots or transport auth (roles come from an environment
  variable). SDK `mcp>=1.19,<2` (`pyproject.toml:28`), locked at 1.30.0 (`requirements.lock:1033`).
- Value here: structured tool output with a declared output schema is worth having: the adapter
  could validate every result against it and bind evidence values by schema instead of "first 40
  top-level scalars" (`mcp/adapter.py:176-186`). Resources fit the knowledge corpus and dataset
  coverage, but the in-process path gains nothing from them. Prompts: the versioned registry is
  already stricter. Sampling: reject; a server asking the client's model for text breaks ADR 0001
  and 0002. Elicitation: reject for approvals; an approval must come from an authenticated second
  person through the gateway, not from whoever sits at the MCP client.
- Pin: `<2` is a sound defensive pin with a hashed lock. It becomes a liability only if a 2.x line
  exists and 1.x stops getting fixes; I could not check that offline. The exposure is small
  because the product path does not use the protocol.
- "One server per domain" (ADR 0004): as deployed it is eight namespaces in one process. It earns
  its keep as a risk and review boundary (per-server trust list, per-server catalogue hash). It
  does not yet earn it as isolation, because nothing runs the servers apart.
- Measure: share of tool results validated against an output schema (today 0 %); contract suite
  passing over stdio in CI (today one round-trip test).
- Cost: output schemas and validation 3 plan-days; stdio contract job 1 plan-day.
- Verdict: structured output, adopt now; resources, later and only with a real out-of-process
  deployment; prompts, sampling, elicitation, not worth it.

**(d) Agent-to-agent protocols such as A2A (assessment only)**
- Present now: none; agents exchange typed `AgentMessage` envelopes built by the harness
  (`harness/common.py:195-206`), in one process.
- Value here: none. A2A addresses discovery and delegation between independently operated agents.
  CEAP's specialists are deterministic functions under one harness and one policy engine; a
  network protocol between them would add an unauthenticated surface and remove nothing.
- Verdict: not worth it. Revisit only if a second, separately owned system has to call CEAP, in
  which case the existing HTTP API with its auth is the boundary.

**(e) Agent skills (packaged, versioned procedures the planner can select)**
- Present now: the canonical plan (`llm/client.py` `canonical_plan`), the per-agent requirement
  table (`agents/requirements.py`) and the enumerated hypotheses and baselines are skills in all
  but name, but the model is asked to emit raw steps, which a 7B model gets wrong half the time.
- Value here: the highest of the six. Let the planner choose one of a handful of named, versioned
  procedures (for example `shortfall_attribution@1`, `venue_degradation@1`, `latency_incident@1`)
  plus the enumerated scope, and let the harness expand it. The output space shrinks from a step
  list to an enum, which is what small local models do well.
- Measure: against the current baseline on the same gold questions and seeds: plan-valid rate
  (now 15/30), required-tool recall (now 0.73), repairs per task, planner tokens and latency, and
  golden primary-cause accuracy (now 6/9) to confirm no loss. Pre-register "valid >= 27/30".
- Cost: 4 to 5 plan-days; one local evaluation run of the same size as 0.9.0; no licence.
- Verdict: adopt now. Keep free-form planning behind a flag as the challenger.

**(f) Plan-level evaluation: trajectory and tool-call accuracy**
- Present now: set-based recall on the raw reply, precision against a global allowed list,
  plan-valid, repair and fallback rates (`eval/agents.py:77-146`); ten gold questions, one symbol,
  one dataset.
- Value here: needed to make the "agent" claim testable (AI-2-12).
- Add: per-question precision against gold; argument-exact-match rate; baseline-choice and
  hypothesis-ranking accuracy; executed-trajectory metrics (model-originated share of executed
  steps, redundant calls, replans, dispute checks run and their hit rate); the counterfactual
  golden score, model plan versus canonical plan; gold plans on at least three symbols and the
  incident datasets.
- Measure: all are computed from stored cassettes and results; the 0.9.0 recordings can be
  re-scored without a new model run for everything except the counterfactual.
- Cost: 3 plan-days for the scorer and gold extension; the counterfactual needs one replay-only
  pass (minutes) if the canonical run is deterministic, which it is.
- Verdict: adopt now.

**(g) Other things missing and worth it**
- A resume entry point and persisted approval interrupt (AI-2-01, -02, -08): 3 to 4 plan-days;
  adopt now; it is what makes G5 true.
- Idempotency key handed to mutating tools, intent rows (AI-2-04): 2 plan-days; adopt now, before
  a second mutating tool exists.
- Policy inside the standalone server (AI-2-03): 1 plan-day; adopt now.
- Replay that checks prompts and the live status (AI-2-10): 1 plan-day; adopt now.
- Policy rules as data with a decision-table test (every rule x role x risk level enumerated and
  snapshotted): 2 plan-days; later; it would make ADR 0002's "a wrong rule is a real hole"
  reviewable by a non-programmer.
- Not worth it: a framework rewrite, vector memory, multi-agent debate, A2A.
