# AI-1: LLM layer review (CEAP v0.9.0, 2026-11-12)

Reviewer: AI-1. Area: `src/ceap/llm/`, the local-model evaluation (`eval/local_model.py`,
`eval/challenger.py`, `eval/stats.py`, `eval/agents.py` as it scores the model), the LLM kill switch,
`observability/ai_slo.py`, local-model cassettes and committed results.

## 1. Scope covered, and what I did not get to

Read in full: `llm/ollama.py`, `llm/router.py`, `llm/schemas.py`, `llm/prompts.py`, `llm/pins.py`,
`llm/model_pins.toml`, `llm/models.py`, `eval/local_model.py`, `eval/challenger.py`, `eval/stats.py`,
`eval/agents.py`, `persistence/killswitch.py`, `docs/evaluation/local_model/README.md`.
Read in part: `llm/client.py` (Anthropic adapter request/retry path, not the 700 lines of mock
narrative), `llm/recording.py` (header, provenance, transport), `observability/ai_slo.py`,
`observability/tracing.py`, `agents/planner.py` (validation, repair, sanitiser), `agents/reporter.py`
(`validate_claims`, `audit_numbers`), `agents/base.py`, the two local-model test files,
`docs/learn/ai/01-llms.md`, `07-local-models.md`, CHANGELOG 0.9.0.

Three read-only verification scripts were run against the committed cassettes (no Ollama, no
files written): (a) the golden slice replayed through the same router construction as
`run_seed`, with a counting client; (b) `planner_eval` replayed for seeds 1-3 with the validator
errors logged; (c) the claims replayed with the drop reasons logged. Their outputs are quoted in
the findings.

Not covered: the 10 hand-built Anthropic cassettes and `tests/cassettes/golden.py`; the mock
narrative generator in `client.py`; `eval/judge.py` and `eval/injection.py` beyond their entry
points; `docs/evaluation/evaluation.md` and `latest.json` beyond the local-model paragraphs; the
router and Anthropic-adapter tests were listed, not read line by line. I could not determine why
the *live* golden run recorded no model call (AI-1-01); only the replay behaviour is verified.

## 2. Score: 4 / 10 against a top-firm bar

- The engineering skeleton is good: grammar-constrained output, strict two-stage validation,
  exact-argument evidence lookup, nonce-delimited untrusted text, prompt hash pins, budget
  reservation and a breaker. That part would score 7.
- The measurement on top of it does not survive five minutes of checking. The published
  "golden slice" local-model numbers are produced entirely by the mock fallback. The headline
  "plan valid 15/30" is 14 parts validator quirk to 1 part model error. The "0/39 untraceable
  numbers" sentence claims a guardrail effect that the data does not contain.
- The local model is not a runtime option; the model pin by digest is recorded, not enforced;
  the AI SLO monitor is not wired into the running platform and its two main indicators cannot
  see agent-level repairs.
- Honest disclosures (challenger not measured, labels written by a model, pooled CI too narrow)
  are a real strength and were verified.

## 3. What is good (verified)

- Constrained decoding is real. `chat_body` sends the Pydantic JSON schema as Ollama `format`
  (`src/ceap/llm/ollama.py:86-87, 100-101`), and it works: all 43 + 12 + 12 planning replies in the
  committed cassettes parse against the per-task schema with zero parse errors (script b).
- Tool ids outside the catalogue cannot be expressed: steps are a union of one model per tool with
  a `Literal` id (`src/ceap/llm/schemas.py:139-147`).
- Specialists read tool output by exact task-derived arguments (`find_exact`,
  `src/ceap/agents/base.py:133`), so a model-chosen `cost_multiplier` or `execution_lag_days` in a
  plan step cannot become the number a finding reports. This is the strongest piece of the
  "model never computes" boundary.
- Data-scoping arguments are re-pinned to the task whatever the model wrote
  (`src/ceap/agents/planner.py:398-403`).
- Untrusted text: NFKC, zero-width strip, tag neutralisation and a per-block random nonce
  (`src/ceap/llm/prompts.py:127-166`). Prompt version and sha travel in request metadata and into
  `LLMCallRecord` (`src/ceap/llm/router.py:317-335`); a drifted prompt file is refused at load
  (`prompts.py:76-96`).
- Budget check reserves before the call, under a lock (`router.py:230-273`).
- Wilson interval is implemented correctly (`src/ceap/eval/stats.py:17-27`); 15/30 gives
  [0.3315, 0.6685], matching the committed JSON.
- `OllamaLLMClient` refuses a checkpoint from another (model, seed, temperature)
  (`ollama.py:226-234`).
- Disclosures that hold up: challenger not measured (README:74-79), pooled interval too narrow
  (README:83-85), judge labels not human (README:93-95).

## 4. Findings

### AI-1-01 [P0] The published local-model "golden slice" numbers are 100 % mock fallback; qwen answered none of those calls
- Evidence: `src/ceap/eval/local_model.py:94` (`router = LLMRouter(client, fallback=MockLLMClient())`),
  `src/ceap/llm/router.py:404-417` (any exception, including `CassetteStaleError`, is answered by the
  fallback), `src/ceap/llm/ollama.py:313-314`, `docs/evaluation/local_model/README.md:44-47, 88-92`,
  `CHANGELOG.md:20, 67`, `README.md:19, 160`.
- Status: VERIFIED. Replaying seed 1's golden slice with a counting client: primary ok `{}`,
  primary errors `planning: CassetteStaleError x2, critique x1, claims x1, narrative x1`, then the
  breaker opened; `router.usage = {'calls': 15, 'fallbacks': 15}`; scores 0.6667 / 1.0 / 0.6667 /
  1.0, exactly the published row. The cassettes hold 13 / 14 / 16 planning replies, which is exactly
  10 planner cases plus repairs minus one failed call; there is no golden interaction in any seed.
  The three seeds are identical to four decimals at temperature 0.7 for the same reason.
- Confidence: high.
- Why it matters at a top firm: "golden cause 6/9, verdict 3/3" is printed in the release
  highlights, the root README and the learning pages under evidence class `local_model`. It is the
  deterministic mock. README:90-91 also says "The model's replies are identical in both"; there are
  no model replies. A validator who reruns one command finds this, and then trusts nothing else.
  The project's own rule (`src/ceap/llm/recording.py:23-24`: a run that silently skipped a model
  call is not a pass) is not applied to the local path.
- Fix: (1) withdraw the four golden rows everywhere and say "not measured". (2) In replay,
  build the router with `fallback=None`, or make `CassetteStaleError` non-recoverable in
  `LLMRouter`. (3) Add `assert_consumed` and a fallback count to `run_seed`; write
  `router.usage` and the SLO snapshot into the result file. (4) Derive the evidence class from
  the responses actually returned (`response.model`, `fallback_reason`), not from
  `router.primary` (`recording.py:320-322`). Acceptance test: a replay of a cassette with one
  interaction removed fails; a result with `fallbacks > 0` cannot carry `evidence_class:
  local_model`; the CI replay test asserts the golden block as well as the agent block.

### AI-1-02 [P1] "Plan valid 15/30" measures a validator quirk: 14 of 15 invalid plans fail only on `"dataset": null`
- Evidence: `src/ceap/llm/schemas.py:267-270` (`key in args and str(args[key]) != str(pinned)`),
  `src/ceap/llm/schemas.py:130-134` (optional arguments typed `T | None`, so the grammar offers
  `null`), `src/ceap/agents/planner.py:383-384, 403` (the sanitiser skips `None` and pins `dataset`
  anyway), `docs/evaluation/local_model/README.md:35, 49`, `docs/learn/ai/01-llms.md:34, 40`.
- Status: VERIFIED. Replaying `planner_eval` for seeds 1-3 with the errors logged: 43 attempts, 15
  with errors, 73 errors in total, of which 72 are `steps.N: dataset None is not the task dataset
  'T01'` and 1 is `plan has 5 VALIDATION steps`. Every attempt was schema-valid.
- Confidence: high.
- Why it matters at a top firm: the headline reading "a 7B model produces a valid plan about half
  the time" is wrong. With `null` treated as "omitted" (which is what the enforcement code already
  does) the first-attempt rate is about 28-29/30. The defect also cost 14 repair calls at 10-25
  minutes of CPU each, and it inflates the "repair" rate that the SLO objective (<= 0.20) is
  meant to watch. A diagnosis that stops at the rate without opening the error list is the kind
  of thing a model-validation reader checks first.
- Fix: in `plan_semantic_errors`, skip `args[key] is None` for pinned keys (and for
  `start`/`end`); better, drop `null` from optional members in the schema sent as `format`.
  Re-score from the existing cassettes (no model run is needed for first attempts) and publish
  both numbers with the cause. Acceptance test: a plan whose only deviation is `dataset: null`
  validates; the per-case error list is written into the result JSON.

### AI-1-03 [P1] "0/39 untraceable numbers ... the guardrail, not the model, keeps the report honest" is not what the data shows
- Evidence: `src/ceap/eval/local_model.py:70-78`, `docs/evaluation/local_model/README.md:42, 51-53`,
  `CHANGELOG.md:20`, `docs/learn/ai/07-local-models.md:32`.
- Status: VERIFIED. The metric counts claims *dropped for* an untraceable number. It is 0 because
  the model proposed none. Replaying the claims: 17 drop reasons over 14 dropped claims: 8 `fact
  key ... not in facts`, 8 `evidence id ... unknown` (the model cited chunk ids such as `CH-ESC`
  where the harness wanted `EV-R-KNOW`), 1 `no fact keys and no evidence ids`. None is a number.
- Confidence: high.
- Why it matters at a top firm: the row label says "reaching the report" (it measures the
  opposite side), and the prose credits the number guardrail with a save it never made in this
  run. README:51-52 also says the dropped third were "not traceable to tool output"; they were
  dropped for wrong key paths and an id-namespace mismatch, half of which is arguably a prompt
  or interface problem, not unfaithfulness. "Faithfulness 25/39" is therefore "share of claims
  that passed structural validation".
- Fix: rename the row to "claims proposed with an untraceable number: 0/39 (guardrail not
  exercised)"; publish the drop-reason breakdown; rename `faithfulness` to `claims_kept`. Show
  the allowed fact-key paths and evidence ids in the claims prompt and re-measure. Acceptance
  test: the result JSON carries a `drop_reasons` histogram and the README table is generated
  from it.

### AI-1-04 [P1] The model "pinned by digest" is pinned by name only; the digest is recorded and never checked
- Evidence: `src/ceap/llm/ollama.py:49-51, 210-211` (allow-list of tags), `ollama.py:320-331`
  (`model_digest` returns `""` on any error), `src/ceap/eval/local_model.py:171-173`,
  `src/ceap/llm/model_pins.toml:9-13` (hosted ids only), `docs/learn/ai/07-local-models.md:9-10, 21`,
  `docs/evaluation/local_model/README.md:108`.
- Status: VERIFIED (read the code; no comparison of a digest with an expected value exists).
- Confidence: high.
- Why it matters at a top firm: "CEAP pins the exact model weights by digest" and "a live run
  needs Ollama with the pinned digest" are stated; nothing enforces either. A moved tag, or
  Ollama not answering `/api/tags`, produces a run with an empty or different digest and no
  error. The Ollama server version, quantisation and `num_ctx` are not in the provenance block
  at all (`ollama.py:125-134`), so the mid-run upgrade to 0.40.16 cannot be verified or bounded
  from the artefacts; the disclosure rests on the author's word. A checkpoint resume can mix
  server versions inside one cassette without trace.
- Fix: add `[local_models]` to `model_pins.toml` with tag -> digest; refuse a live call when
  `/api/tags` is unreachable or the digest differs; record `/api/version`, the `/api/show`
  parameters and a timestamp per interaction. Acceptance test: a transport returning another
  digest makes `OllamaLLMClient` raise before the first chat call.

### AI-1-05 [P1] AI SLO monitor: not wired into the platform, and its plan-valid and repair indicators cannot see agent repairs
- Evidence: `src/ceap/observability/ai_slo.py:301` (`default_monitor = None`, never assigned in
  `src/`), `src/ceap/llm/router.py:203, 350`, `ai_slo.py:118-132` (repair = rung 2 on the
  response), `router.py:396-403` (the router attaches rung 2 only for `max_tokens` / `refusal`),
  `src/ceap/agents/planner.py:190-210` (the real repair happens in the agent),
  `ai_slo.py:17-18` ("fed by the reporter": no caller in `src/`).
- Status: VERIFIED by reading and by grep; in the golden replay the monitor saw fallback = 1.0
  on every purpose and raised no alert (n = 3-4 < `min_n` = 10), and the published pooled file
  keeps `slo_alerts: []` while dropping the snapshot (`local_model.py:186-191`).
- Confidence: high.
- Why it matters at a top firm: CHANGELOG B8 reads as runtime drift monitoring. In the running
  platform nothing records to it. Where it is used, the planner indicator would have shown plan
  valid 1.00 and repair 0.00 for qwen while the evaluation says 0.50 and 0.47, because the
  router sees only schema validity. `untraceable_number` has no feed. An SLO that cannot move is
  worse than none.
- Fix: have the agents report plan validity, repair and claim drops to the monitor (one
  `record_outcome` call each); set a default monitor in `Platform`; always persist the snapshot
  with a result. Acceptance test: `InvalidPlanner` through the real `PlannerAgent` moves
  `plan_valid` down and `repair` up in the monitor; a 100 % fallback run of any length emits an
  alert or an explicit "insufficient n" row.

### AI-1-06 [P2] Critic never detects a direction (sign) error, and the false-alarm rate is not published
- Evidence: `src/ceap/eval/agents.py:306-314` (computed), `src/ceap/eval/local_model.py:110-122`
  (`RATE_PATHS` omits `false_alarm` and `detection_by_kind`),
  `docs/evaluation/local_model/README.md:43`, committed `qwen2.5_7b-instruct_s{1,2,3}.json`.
- Status: VERIFIED from the committed per-seed JSON: `direction_mismatch` 0/3, 0/3, 0/3 (0/9);
  false alarms 1/10, 1/10, 2/10 (4/30).
- Confidence: high.
- Why it matters at a top firm: "improved by 6.4 bps" against evidence of a 6.4 bps rise is the
  most trading-relevant fault in the set and the model misses it every time. A detection rate
  with no false-alarm rate next to it is not a detector measurement. The n = 45 is 15 planted
  faults in 5 prompts, repeated over 3 seeds.
- Fix: add both to `RATE_PATHS` and the README table. Acceptance test: the README table is
  generated and contains a per-kind row and the control row.

### AI-1-07 [P2] Tool precision 1.00 is true by construction and is presented as model behaviour
- Evidence: `src/ceap/llm/schemas.py:145` (`tool_id=(Literal[tool_id], ...)`),
  `src/ceap/eval/agents.py:86, 123, 138`, `docs/evaluation/local_model/README.md:38, 50`,
  `docs/learn/ai/01-llms.md:41`.
- Status: VERIFIED.
- Confidence: high.
- Why it matters at a top firm: with grammar-constrained decoding the model cannot emit an id
  outside the catalogue, and "allowed" in the gold file is the catalogue scope. 161/161 with a
  CI of 0.98-1.00 measures the grammar. Reported as "it never picks a tool outside the task".
- Fix: label it "guaranteed by the schema"; measure precision against each case's
  required-plus-acceptable set instead. Acceptance test: `NarrowPlanner`/a noisy planner moves
  the new precision.

### AI-1-08 [P2] Context window: planning uses up to 8,154 of 8,192 tokens, with no overflow check and no output cap
- Evidence: `src/ceap/llm/ollama.py:97` (`num_ctx: 8192`, no `num_predict`; `request.max_tokens`
  is ignored), `ollama.py:267-270`, `src/ceap/llm/prompts.py:172` (budget 60,000 characters,
  not model-aware).
- Status: VERIFIED from the cassettes: planning input 6,140-6,453 tokens, output up to 1,864;
  the largest sum is 6,297 + 1,857 = 8,154 (seed 3). SUSPECTED consequence: Ollama truncates or
  shifts context silently when the sum passes `num_ctx`; no committed reply shows it.
- Confidence: medium.
- Why it matters at a top firm: the measured run sits 38 tokens from silent truncation of the
  system prompt or tool catalogue. One more tool in the catalogue and plan quality changes for a
  reason no metric reports. The 6k-token prompt is also why a planning call takes a median of
  14-19 minutes.
- Fix: set `num_predict` from `request.max_tokens`; after each call assert
  `prompt_eval_count + eval_count < num_ctx` and mark the response degraded otherwise; make
  `fit_context` take a token budget from the client; send only the tools relevant to the task
  kind. Acceptance test: a transport reporting `prompt_eval_count = num_ctx` yields a
  degradation.

### AI-1-09 [P2] A seeded live run is not repeatable: every request carries fresh random nonces
- Evidence: `src/ceap/llm/prompts.py:162` (`secrets.token_hex`), `src/ceap/llm/ollama.py:11-12`
  ("the seed makes a run repeatable on the same build"), `ollama.py:105-106` (nonces masked
  only in the hash).
- Status: VERIFIED by reading; not run live (rule 4).
- Confidence: high that the prompt bytes differ per run; medium on the size of the effect.
- Why it matters at a top firm: at temperature 0.7 a different prompt gives a different sample,
  so re-recording seed 1 on the same server gives different replies. Replay is reproducible;
  the live measurement is not, and the docstring says it is. The checkpoint memo
  (`ollama.py:256-257`) hides this by answering from the file.
- Fix: derive the nonce from an HMAC of (run key, block id, content) in evaluation runs, or
  store the nonces in the cassette. Acceptance test: two `chat_body` calls for the same request
  in an evaluation run are byte-identical.

### AI-1-10 [P2] Result files and local cassettes do not record what was asked: no request text, no prompt id or version, no code commit
- Evidence: `src/ceap/llm/ollama.py:137-143` (`LocalInteraction` has only the hash, the reply
  and a latency), `src/ceap/eval/local_model.py:182-193`.
- Status: VERIFIED.
- Confidence: high.
- Why it matters at a top firm: a result cannot be tied to `planner@3` from the artefact; an
  auditor cannot read the prompt a reply answered; and a hand-edited reply replays cleanly
  because nothing binds the response bytes (no cassette digest list). The hosted-adapter
  cassette format already stores the request (`src/ceap/llm/recording.py:68-75`).
- Fix: store the redacted request and `metadata["prompt"]` per interaction; put prompt refs,
  git commit and Ollama version in the pooled file; pin cassette sha256 values in one manifest.
  Acceptance test: editing one byte of a recorded reply fails CI.

### AI-1-11 [P2] The LLM kill switch is a start-up environment flag on the router only
- Evidence: `src/ceap/config.py:129`, `src/ceap/llm/router.py:361-362`,
  `src/ceap/agents/base.py:22-24` (`getattr(llm, "disabled", False)`),
  `src/ceap/persistence/killswitch.py:1-9` (the DB switch covers staging and the paper job only),
  `src/ceap/eval/local_model.py:89-90, 175`.
- Status: VERIFIED.
- Confidence: high.
- Why it matters at a top firm: (1) it cannot be thrown at run time without a restart; the
  audited DB switch does not stop model calls. (2) Any `LLMClient` that is not an `LLMRouter`
  has no `disabled` attribute, so `Platform(llm=<client>)` and every evaluation runner bypass
  it, together with the budget and the breaker. (3) On error it is sound: an unparsable value
  raises `SettingsError` (`config.py:50`), which is fail-closed. The DB switch reads "off" on an
  old schema (`killswitch.py:41-42, 47`), which is fail-open, though outside the LLM path.
- Fix: check the flag in `BaseAgent.call_llm` (`base.py:67`) from one source that also reads
  the DB switch; make `Platform` wrap a bare client in a router. Acceptance test: with the
  switch set mid-run, the next `call_llm` raises `LLMDisabledError` for every client type.

### AI-1-12 [P2] The local model is not a runtime provider; the "router" does not route local models
- Evidence: `src/ceap/config.py:29` (`LLM_PROVIDERS = ("mock", "anthropic")`),
  `src/ceap/llm/router.py:459-474`, `README.md:112-113` (disclosed).
- Status: VERIFIED; JUDGEMENT on severity.
- Confidence: high.
- Why it matters at a top firm: the owner decision is local open-weight models and no paid API,
  yet the only real model the product can run on is the hosted one. The cascade is: refusal ->
  second hosted id; exception -> mock. There is no local primary with a local challenger, no
  validity-based escalation, and per-call USD is always 0 for a local model because prices
  exist only for hosted ids.
- Fix: add provider `ollama` to `build_router` behind the same gates; see section 6 (b).
  Acceptance test: `CEAP_LLM_PROVIDER=ollama` with a mock transport runs one investigation and
  every `LLMCallRecord` names the local model.

### AI-1-13 [P2] The challenger hook in the approver view is unreachable in the product
- Evidence: `src/ceap/harness/common.py:53, 276` (the key is only read; grep finds no writer in
  `src/`), `tests/unit/test_challenger_approver_view_090.py:54` (the test plants the key).
- Status: VERIFIED by grep.
- Confidence: high.
- Why it matters at a top firm: B7 "plan agreement shown to the approver" is neither measured
  (disclosed) nor produced by any code path. `challenger --replay` would also fail on a missing
  `challenger_primary` cassette (`src/ceap/eval/challenger.py:131, 147`).
- Fix: either wire a challenger planning call behind a flag or mark B7 as not delivered.
  Acceptance test: an integration test in which the approval payload carries the block without
  the test writing the scratch key.

### AI-1-14 [P2] Pooled Wilson intervals are the headline although the effective sample is 10 cases, not 30
- Evidence: `src/ceap/eval/local_model.py:139`, `docs/evaluation/local_model/README.md:35-47`
  (disclosed at 83-85).
- Status: VERIFIED; JUDGEMENT on presentation.
- Confidence: high.
- Why it matters at a top firm: the disclosure is honest, but the table, CHANGELOG and README
  quote 0.33-0.67 as "95 % CI". Recall pools 84 tool slots from 10 plans; critic pools 45 from 5
  prompts. The correct interval resamples cases (clusters), with seeds inside.
- Fix: add a case-level cluster bootstrap to `eval/stats.py` and publish that as the interval;
  keep Wilson per seed. Acceptance test: for identical seeds the cluster interval equals the
  single-seed interval, not a narrower one.

### AI-1-15 [P3] Smaller points
- Evidence and notes, all VERIFIED by reading, confidence high unless stated:
  - `docs/evaluation/local_model/README.md:22`: "reporter cases from successful plans only" is
    wrong; the 4 cases are hand-built (`src/ceap/eval/agents.py:336`), n varies with the claim count.
  - `reporter.citation_verified` is 0/0 for the local model (the Ollama adapter returns no
    citations, `src/ceap/llm/ollama.py:264-274`); the README drops the row without saying so.
  - Published `latency_ms` comes from replay and counts repeated lookups (claims n = 12 for 4
    recorded calls; `ollama.py:316`).
  - `src/ceap/llm/pins.py:56`: an unknown purpose silently gets the `narrative` pin (`claims`,
    `general` and any typo).
  - `src/ceap/llm/router.py:393-395`: the refused first call's tokens are never metered and the
    second call is not counted against the per-task call cap.
  - `src/ceap/llm/client.py:856`: backoff has no jitter and ignores `Retry-After`; the Ollama
    client has no retry and a 30-minute timeout (`ollama.py:205`).
  - `src/ceap/llm/ollama.py:27-28`: "nothing constructs this client unless CEAP_LOCAL_MODEL=1" is
    enforced only in the four `main()` functions, not in the constructor.
  - Numeric tool arguments chosen by the model are type-checked only
    (`src/ceap/agents/planner.py:388-391`): a plan may run `backtest.run_backtest` with any
    `cost_multiplier`. Findings are protected by `find_exact`; the extra evidence still exists in
    memory (SUSPECTED low impact).
  - The number audit is value-set membership over all facts (`src/ceap/agents/reporter.py:754-781`):
    a correct number attached to the wrong metric passes. ADR 0001's formal statement (a fact
    "that renders to that token") is met; a reader will assume key binding (JUDGEMENT).
- Fix: correct the three doc lines; raise on unknown purpose; add jitter; move the live gate
  into `OllamaLLMClient.__init__`; clamp numeric arguments to the tool schema defaults unless
  the task names them.

## 5. Earlier plan (2026-11-09) items for this area

| Item | Promise | Status |
|---|---|---|
| A15 | Verify prompt hashes and model pins at load | Done and convincing for prompts (`prompts.py:76-96`). Not done for the local model (AI-1-04). |
| B1 | Local model behind `LLMClient` with schema output; golden, adversarial and claims at n >= 3 seeds | Adapter done and good. Agent evals at 3 seeds done. Golden not measured on the model (AI-1-01). Adversarial run at one seed on 40 of 200 attacks. |
| B3 | Metric sensitivity test | Done on mocks (`eval/agents.py:533`); it did not catch that precision cannot move under the real grammar (AI-1-07) or that the SLO cannot see repairs (AI-1-05). |
| B4 | ~200 attacks against the local model nightly | Corpus done. The nightly needs Ollama, which no runner has, so it never runs; 40 attacks once. Done but weak, disclosed. |
| B5 | Judge calibrated against 50 human labels | Labels are model-written (disclosed). One trivial dimension enabled. Done but weak. |
| B7 | Challenger plan agreement shown to the approver | Not done: not measured (disclosed) and not wired (AI-1-13). |
| B8 | Runtime AI SLOs with OTel GenAI spans and SPC | Library done; not wired; indicators blind to agent repairs (AI-1-05). Done but weak. |
| B9 | Docs say only planner, critic, reporter use the model | Done (`eval/agents.py:3`, learning pages). |
| I1 | ADR "LLM never computes" | Done; accurate for findings, looser than it reads for claim text (AI-1-15). |

## 6. Candidates: present now, value, how to measure, cost, verdict

All measurement below runs locally and free. Baseline = the three committed qwen cassettes after
AI-1-01/02/03 are corrected.

**(a) Constrained decoding through Ollama `format`.**
Present now: yes, `src/ceap/llm/ollama.py:86-87, 100-101`; 67/67 planning replies schema-valid.
Value left: remove `null` from optional members and encode what is now "semantic" in the grammar:
`start`/`end` as an enum of the allowed window pairs, `dataset`/`symbol` as `const`. That turns
the remaining validator failures into impossibilities and shortens output (fewer tokens at 5
tok/s). Measure: first-attempt validity and output tokens per plan on the 10 gold cases x 3
seeds, before and after, case-clustered interval. Cost: 1-2 plan-days; about 9 CPU-hours to
re-record three seeds; no licence. Verdict: adopt now. It is the cheapest large gain in the area.

**(b) Cascade and routing with per-call cost and latency.**
Present now: purpose -> hosted model id, refusal -> second hosted id, exception -> mock, breaker,
budget (`src/ceap/llm/router.py:363, 382-395, 404-417`); latency, tokens and USD per call in
`LLMCallRecord` (`router.py:321-338`). Nothing for local models; USD is 0 for them. Value: a local
cascade (qwen first; llama only when the plan is semantically invalid after one repair; mock last)
with cost expressed as CPU-seconds and tokens. Measure: on the gold cases, the share reaching each
rung, end-to-end validity and total CPU-seconds, against qwen alone. Cost: 3 plan-days, about 12
CPU-hours, no licence. Verdict: later. Do the `ollama` provider and CPU-second cost first (1
plan-day, adopt now); a second 8B model of similar strength is unlikely to rescue many plans, so
the cascade itself should wait for evidence from the challenger run.

**(c) OpenTelemetry GenAI semantic conventions.**
Present now: attribute dict and an event, `src/ceap/observability/ai_slo.py:96-111, 199-202`; no
tracer is passed in the platform, `gen_ai.system` is hard-coded to `"ceap"` (it should name the
provider, e.g. `ollama`), there is no real span with duration, no `gen_ai.response.finish_reasons`,
no `gen_ai.request.temperature`/`seed`. Value: modest for a single-user local platform; real if the
project is shown as production-grade observability. Measure: an in-memory exporter test asserting
one `chat {model}` client span per routed call, parented under the agent span, with the listed
attributes. Cost: 1 plan-day, no compute. Verdict: adopt now only as part of fixing AI-1-05 (same
code); otherwise not worth separate effort.

**(d) Evaluation regression gates in CI per model and prompt version.**
Present now: a `slow` replay test that compares two agent metrics with the committed file
(`tests/evaluation/test_local_model_090.py:129-150`); prompt hash pins. Missing: golden coverage,
consumption checks, any gate on a *new* recording. How, without GPU or Ollama in CI: two tiers.
Tier 1 in CI: replay with no fallback, assert every interaction consumed and every published
number reproduced; a prompt or schema change makes cassettes stale and fails the build until
re-recorded, which is the gate. Tier 2 on the owner's machine (a self-hosted runner or a manual
script, free): re-record, then compare new against old with a paired case-level test (McNemar on
the 10-30 paired cases, or the cluster bootstrap difference) and refuse the new cassettes if the
lower bound of the difference is below a margin fixed in advance. Value: high; it would have
caught AI-1-01. Cost: 2 plan-days for tier 1, 2 for tier 2; each re-record about 9 CPU-hours.
Verdict: tier 1 adopt now; tier 2 later, and only with more cases (10 cases cannot detect less
than a roughly 40-point change).

**(e) Prompt optimisation done honestly.**
Present now: none; prompts are hand-written and versioned. Value: tool recall 0.73 and the claims
drop reasons (wrong key paths, wrong id namespace) look prompt-fixable. But there are 10 planner
cases and 4 reporter cases; any search, DSPy-style or manual, will overfit and a held-out split of
3 cases proves nothing. Measure honestly: first write at least 60 planner cases and 30 reporter
cases, freeze a 50 % holdout, tune on the rest, open the holdout once. Cost: 4-6 plan-days for the
cases, 20-40 CPU-hours per tuning round at current latency, no licence. Verdict: not worth it now.
Fix the interface causes by hand (show allowed fact keys and evidence ids; say "omit optional
arguments"), record as `planner@4`, re-measure. Revisit automated optimisation only after the
case set is five times larger and the prompt is short enough to iterate.

**(f) Other things worth doing.**
- Cut the planning prompt. 6.1-6.4k input tokens for a 10-tool decision is the reason a plan
  takes 14-19 minutes and sits at the context limit. Send only the tools for the task kind and
  compact schemas; keep the system prefix byte-stable so Ollama's prompt cache is reused.
  Measure: input tokens, median latency, recall. Cost 2 plan-days. Adopt now.
- A temperature-0 run as the measurement of record, with sampling seeds reported as a
  sensitivity. One deterministic pass per case is easier to defend than three correlated
  samples. Cost: 3 CPU-hours. Adopt now.
- More cases before more seeds. 30 distinct cases at one seed carry more information than 10
  cases at three. Cost: 2 plan-days of case writing. Adopt now.
- Cassette manifest and request capture (AI-1-10). Cost 1 plan-day. Adopt now.
- Not recommended: a vector of extra frameworks (guardrail libraries, agent frameworks, a
  hosted tracing product). The existing validators are stricter than what those add.
