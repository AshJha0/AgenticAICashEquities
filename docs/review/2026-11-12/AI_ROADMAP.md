# CEAP AI roadmap: gap analysis and target design (2026-11-12)

Reviewed at tag v0.9.0 (`dd6bf3c`). Evidence is cited as `file:line`; the full reasoning for each
item is in the reviewer reports under `reviewers/` (AI-1, AI-2, AI-3, Q, X, E). Everything
recommended here runs locally and costs nothing. Two items need a free model download and one needs
a contact string chosen by the owner; they are listed as owner decisions in `PLAN_0.10.0.md`.

## 1. Where the AI side stands

CEAP is a governed pipeline with three bounded model steps (planner, critic, reporter). The harness
always runs the five specialists and schedules their data reads (`src/ceap/harness/common.py:32`,
`src/ceap/harness/planning.py:304-327`); the model adds optional reads, picks one baseline from a
list and ranks pre-registered hypotheses. That is a sound design for a regulated domain, and
`docs/ai-agents/ai-agents.md` describes it honestly. The control side is the strongest part of the
project: policy before every call, single-use grants, four-eyes by named person, hash-bound
approvals, nonce-delimited untrusted text, grammar-constrained output, prompt hash pins.

The measurement side is where it falls short of a top-firm bar. A reader who knows the field will
check four things in the first few minutes, and three of them do not hold today:

| Question a senior reader asks | Answer at v0.9.0 |
|---|---|
| Was a real model measured end to end? | No. The agent-level numbers are real; the end-to-end "golden slice" (cause 6/9, verdict 3/3) is mock output. Replaying seed 1: 0 cassette hits, 15 mock answers (`src/ceap/eval/local_model.py:94`, `src/ceap/llm/router.py:404-417`). The live per-seed files say 0/3. |
| Do the metrics measure what their names say? | Partly. "Plan valid 15/30" mostly counts a validator rule that rejects `"dataset": null` (`src/ceap/llm/schemas.py:267-269`); "untraceable numbers 0/39" is set membership, so a wrong metric with a matching value passes (`src/ceap/agents/reporter.py:741-751`); "tool precision 1.00" is guaranteed by the grammar (`src/ceap/llm/schemas.py:145`). |
| Are the sample sizes and labels independent? | No. 10 planner cases, 15 planted critic faults, a 1,922-word corpus of the project's own notes, judge labels generated from five templates (`src/ceap/eval/judge.py:137-147`). The docs say most of this; the headline lines do not. |
| Is the safety story enforced or described? | Enforced, and well, inside the harness. Two edges are open: the standalone MCP execution server stages paper orders on capability alone (`src/ceap/mcp/server.py:213-217`), and the ingestion injection screen is six regexes with no normalisation (`src/ceap/rag/ingestion.py:53-62`). |

So the order of work is fixed by the evidence: first make the existing numbers true, then make the
samples large and independent enough to mean something, and only then add capability. Adding
features on top of the current measurement would produce more numbers of the same quality.

## 2. Target design

What a current agentic platform in this domain should have, what CEAP has, and the action.

| Layer | Target | CEAP at v0.9.0 | Action |
|---|---|---|---|
| Structured output | Grammar-constrained JSON; semantic constraints in the schema where possible | Ollama `format` with JSON schema is in use (`src/ceap/llm/ollama.py:86-87, 100-101`); semantic rules live in a validator that disagrees with the sanitiser | Fix the validator; move windows, dataset and symbol into the schema as enums and constants |
| Planning | The model chooses among named, versioned procedures; free-form steps only as a challenger | The model emits raw steps; a 7B model spends 14-19 minutes and about 6k input tokens per plan | Planner skills (an enum of procedures the harness expands); cut the prompt |
| Model routing | One provider interface; local and hosted; cost and latency per call; fallback visible in provenance | Router handles hosted ids and the mock; Ollama is not a runtime provider (`src/ceap/config.py:29`); a fallback does not change the evidence class | Add `ollama` as a provider with CPU-seconds as cost; refuse the `local_model` label when any call fell back |
| Orchestration | A typed graph: nodes, guarded edges, a checkpoint per node, approval as a persisted interrupt | A lifecycle table (`src/ceap/harness/state_machine.py:32-50`); the graph is implicit in `engine.py`; an approval is an awaited future that dies with the process | A thin graph table over the existing engine. No framework. |
| Durability | Resume by task id after a crash, with no repeated model or tool call and no second approval | Tool calls are journalled; plan and agent results are not; no route resumes a task; a resumed approval asks again | Resume entry point; journal plan and agent results; stable approval hash |
| Memory | Human-decided cases only, owner-scoped, persisted, expiring, shown to help by an A/B | Auto-approvals stored as human-decided (`src/ceap/harness/approvals.py:121-122`); no owner boundary or expiry; off in every evaluation | Fix the two defects; keep it off; measure before enabling |
| Retrieval | Hybrid retrieval measured on a real corpus with independent labels and an answerability set | BM25 default, reranker opt-in, 65 chunks, author-written queries | SEC EDGAR corpus with a labelled query set; fuzzy lexical matching first |
| Groundedness | A check that the cited passage supports the claim | The audit checks that the quote is a chunk (`src/ceap/agents/reporter.py:999-1034`); n = 0 with a real model | Deterministic support check now; NLI scorer with the real corpus |
| Guardrails | Deterministic invariants as the control; a small classifier as a tripwire; property fuzzing of the invariants | Invariants are strong; ingestion screen recall 10/50 on the repo's own attacks | Normalise before matching; small injection classifier, advisory; Hypothesis fuzzing of the deterministic layer |
| MCP | Tools with declared output schemas, validated; policy inside every server; resources only when a server runs out of process | Tools only, in process in the product path; standalone execution server has no approval gate | Output schemas and validation; policy in the standalone server |
| Evaluation | Every published number reproducible by a CI replay with no fallback; case-clustered intervals; a rule baseline next to each model metric; a paired gate for re-recordings | One replay test on two metrics (`tests/evaluation/test_local_model_090.py:147-148`); pooled Wilson intervals over identical cases | Replay gate with consumption check; cluster bootstrap; baselines; more cases before more seeds |
| Observability | One span per model call and tool call under the task span, GenAI attributes, SLOs fed in production | The SLO monitor exists but is not wired (`src/ceap/observability/ai_slo.py:301`); the tracer export has no caller (`src/ceap/observability/tracing.py:155`) | Wire the monitor and spans, or delete the dead code |
| Model risk | Digest-verified model at call time; prompt id and version, commit and runtime version stored with each result | Tag allow-list only (`src/ceap/llm/ollama.py:210-211`); cassettes store the hash and the reply, not the request | Verify digest; cassette manifest with request text, prompt version, commit, Ollama version |

## 3. Candidate table ("AI not used yet")

One row per candidate from the brief. "Baseline" is the committed number the candidate would be
measured against. Effort is in plan-days.

### LLM and agents

| Candidate | Present now? | Value here | How measured | Cost | Verdict |
|---|---|---|---|---|---|
| Constrained decoding / JSON-schema output through Ollama | Yes, working: every planning reply in the cassettes is schema-valid (`ollama.py:86-87, 100-101`) | The remaining failures are one validator rule (`schemas.py:267-269`) and constraints that can move into the schema | First-attempt plan validity on the gold cases, before and after; corrected baseline is about 28-29 of 30 on replay, not 15/30 | 1-2 days; about 9 CPU-hours to re-record | **Adopt now** as a repair, not a new feature |
| Model cascade and routing with per-call cost and latency | Partly: hosted cascade, breaker, budget, latency and tokens per call (`router.py:321-338, 363, 382-417`); nothing for local models | An `ollama` provider with CPU-seconds as cost is needed for anything else to be measured in the product. A qwen-to-llama cascade is unlikely to rescue many plans from a model of similar size | Share of calls reaching each rung, validity and CPU-seconds against qwen alone | Provider 1 day; cascade 3 days and about 12 CPU-hours | Provider **now**; cascade **later**, after the challenger run gives evidence |
| Explicit typed agent graph with checkpoints, conditional edges and human-in-the-loop interrupts | Partly: lifecycle table and tool journal; no node checkpoints; approval is in memory only | Fixes three verified defects at once: plan and agent results not resumed, approvals not resumable, human wait counted against the 300 s deadline (AI-2-01, -02, -05, -08) | Kill-at-every-node test: zero repeated model or tool calls and an equal normalised report; number of governance invariants enforced by the table (today 0 of 3) | 6-8 days, no compute | **Adopt now** in the thin form (a node and edge table driving the existing engine). A framework rewrite is not justified and would add a heavy dependency against the hashed-lock discipline |
| Self-consistency or multi-agent debate for the critic (baseline 35/45) | No | None on the evidence in the repo. A majority vote over the three committed seeds gives 12/15 and keeps the same false alarm; the misses are sign errors (direction 0/9), which the deterministic check already catches 15/15 | Already measurable from committed data | 3x latency for no gain | **Not worth it.** Instead publish the false-alarm rate (4/30) and test whether the model adds anything on findings the rules cannot parse |
| Agent memory with provenance and safety limits | Partly (`src/ceap/harness/memory.py:248-315`): append-only, audited, capped, data-block views. Missing: human-only filter, owner boundary, persistence, expiry | Unproven: disabled in every evaluation | Leave-one-out A/B on the golden set plus a poisoning test | Fixes 2 days; A/B 1 day and one local run | Fix the two defects **now**; A/B **later**; remove the feature if the A/B shows nothing |

### RAG

| Candidate | Present now? | Value here | How measured | Cost | Verdict |
|---|---|---|---|---|---|
| Cross-encoder reranker (approved in D1) | Shipped, opt-in, no production caller (`src/ceap/rag/dense.py:114-138, 187-201`); scores are precomputed for the evaluation queries | +8 of 124 queries, almost all typos; only one of the two rerank variants clears an exact paired test (AI-3-08) | Gated production comparison against 89/124, and latency per query | 0.5 day | **Later**, together with the real corpus. Try fuzzy lexical matching in BM25 first (0.5-1 day, no model): if it recovers the typo queries the reranker is not worth a model dependency at this size |
| GraphRAG / knowledge graph over issuers, sectors, factors | No | None measurable: the corpus has no issuers or sectors, and symbol questions are answered by tools over structured data | No evaluation set exists | 5+ days | **Not worth it** |
| Real document corpus (SEC EDGAR 10-K / 10-Q) | No | The one change that makes the retrieval numbers credible: thousands of chunks, boilerplate, near-duplicates across years, tables | Same five variants and the same pre-registered rule on a new set: at least 300 answerable questions with section-level labels, at least 100 unanswerable on-domain questions, a second labeller on 20 % with agreement reported | 6-8 days; 1-2 CPU-hours to embed; public-domain text; raw files in a git-ignored cache with hashes committed | **Adopt next** (0.11.0). Needs an owner decision: EDGAR requires a declared contact in the User-Agent |

### Guardrails

| Candidate | Present now? | Value here | How measured | Cost | Verdict |
|---|---|---|---|---|---|
| Local safety classifier next to the deterministic checks | No; the only input screen is six regexes (`ingestion.py:53-62`), recall 10/50 on the repo's own attack strings, bypassed by one zero-width character | Direct, measurable lift on a named weakness | Recall on the 200 corpus strings and false-positive rate on the 65 chunks, 159 queries and 10 gold questions, against the regex baseline, Wilson intervals | 1.5-2 days; a small injection classifier (Prompt Guard class) is better suited and far faster on CPU than Llama Guard, whose taxonomy is content harm | **Adopt now, advisory only.** Needs an owner decision for one free model download. First fix the regex screen itself (NFKC, strip zero-width), which costs half a day and no model |
| Automated red-teaming that generates new attacks | No | The useful target is the deterministic layer, not the model | (a) Hypothesis fuzzing of plans and claims against the harness invariants, baseline the strings that pass today (AI-3-03); (b) generated attack text with executable-only denominators against 3/40 | (a) 1-2 days, no model; (b) 2-3 days and about 12 CPU-hours per 200 attacks | (a) **now**; (b) **later** |

### MCP and interoperability

| Candidate | Present now? | Value here | How measured | Cost | Verdict |
|---|---|---|---|---|---|
| MCP structured tool output with declared output schemas | Client reads `structuredContent` (`src/ceap/mcp/client.py:247-252`); servers declare no output schema | The adapter could validate every result and bind evidence by schema instead of the first 40 scalars (`src/ceap/mcp/adapter.py:176-186`) | Share of tool results validated against a schema (today 0 %); contract suite over stdio in CI | 3-4 days | **Adopt now** |
| MCP resources | No | Fits the knowledge corpus and dataset coverage, but the in-process path gains nothing | n/a | 2-3 days | **Later**, only with an out-of-process deployment |
| MCP prompts | No | The versioned, hash-pinned prompt registry is already stricter | n/a | n/a | **Not worth it** |
| MCP sampling and elicitation | No | Negative: a server asking the client's model for text breaks ADR 0001 and 0002; an approval must come from an authenticated second person, not from whoever sits at the client | n/a | n/a | **Do not adopt** |
| Agent-to-agent protocols (assess only) | No; agents exchange typed envelopes in one process (`src/ceap/harness/common.py:195-206`) | None: the specialists are deterministic functions under one harness | n/a | n/a | **Not worth it.** If a second system must call CEAP, the authenticated HTTP API is the boundary |

### Observability and evaluation

| Candidate | Present now? | Value here | How measured | Cost | Verdict |
|---|---|---|---|---|---|
| OpenTelemetry GenAI tracing for LLM and tool calls | Attribute dict and metrics exist (`ai_slo.py:96-111, 199-202`) but the monitor is not wired (`ai_slo.py:301`) and the tracer export has no caller (`tracing.py:155`) | Modest for a single-user local platform; it matters because the README lists it | In-memory exporter test: one client span per routed call under the agent span, with model, tokens, finish reason, temperature and seed | 1-2 days | **Adopt now** as the fix for the unwired monitor; otherwise delete the dead code and the claim |
| Evaluation regression gates in CI per model and prompt version, with confidence intervals | One replay test on two agent metrics | High: it would have caught the P0 | Tier 1 in CI: replay with no fallback, every interaction consumed, every published number reproduced. Tier 2 on the owner's machine: re-record, paired case-level test against the old recording with a margin fixed in advance | Tier 1 2 days; tier 2 2 days and about 9 CPU-hours per run | Tier 1 **now**; tier 2 **later**, once there are enough cases (10 cases cannot detect less than about a 40-point change). No self-hosted runner on a public repo |
| Drift monitoring for the ML models | `ModelRegistry.check_drift` exists with no caller (`src/ceap/ml/registry.py:248`); registered models are stale against the generator hash (Q-10) | Makes the model cards true | A test that fails when a registered model's generator hash differs from the current one; PSI or KS on inputs with a threshold derived from the null, not chosen | 2 days | **Adopt now** |

### ML for quant and execution

| Candidate | Present now? | Value here | How measured | Cost | Verdict |
|---|---|---|---|---|---|
| Local time-series foundation models (Chronos, TimesFM) | No | Low for returns. Plausible for volatility only. The free data (Ken French, FRED) is very likely in the pretraining set, so a good backtest proves little | Pre-registered volatility forecast on the 49 industries against EWMA, GARCH(1,1) and HAR, QLIKE loss, Diebold-Mariano test, a live window after the model's cut-off as the only clean test | 5-6 days; hours of CPU; model download | **Later** (0.12.0), volatility only, with the contamination caveat stated in the preregistration |
| Conformal prediction for pre-trade cost | Already present: `conformal_q` in `src/ceap/ml/pretrade_model.json`. Coverage is below nominal in 9 of 10 cells (lowest 85.9 %) and the acceptance band is 85-95 % (`scripts/train_pretrade.py:152`) | Repair: the queued item is a fix, not an addition | Coverage per cell against nominal with a binomial interval; add a point-error metric and a naive challenger to the model card | 2-3 days | **Adopt now** as a repair |
| Conformal prediction for signals | No | Low until the pre-trade repair shows the method is done properly here | Coverage of forward-return intervals out of sample | 2-3 days | **Later** |
| SHAP-style explanations | No | None: the ridge combination and the three-term pre-trade model are linear, so exact attributions are coefficient times standardised input | n/a | n/a | **Not worth it.** Print the exact linear attributions instead (half a day) |
| Bandits for algo selection | No | None on current evidence: the repo's own table shows the schedulers are indistinguishable in the simulator (about 0.3-0.4 bps at every participation, `CHANGELOG.md:226-228`) | n/a | n/a | **Not worth it** until the simulator separates the algos |
| Reinforcement learning for execution, simulator only | No | Negative for credibility: an agent would learn the artefacts of a stylised book with 300 shares a level | n/a | 8+ days | **Not worth it.** A paired scheduler study against the Almgren-Chriss trajectory is the honest version (3 days) |

## 4. Candidates the brief did not list, and that are worth more than most of the above

| Candidate | Why | Cost | Verdict |
|---|---|---|---|
| Provenance guard on model-labelled numbers | Refuse to write `evidence_class: local_model` when any call in the run was answered by a fallback. Closes the P0 at the root | 0.5 day | Now |
| Planner skills (enum of versioned procedures) | Shrinks the output space from a step list to a choice, which is what a small local model does well. Pre-register "valid at least 27/30, recall above 0.73, no loss on golden cause" | 4-5 days and one local run | Now |
| Shorter planning prompt, stable prefix | About 6k input tokens for a ten-tool decision is why a plan takes 14-19 minutes and sits at the 8,192-token context limit with no overflow check (`ollama.py:97`) | 2 days | Now |
| Temperature 0 as the measurement of record; more cases before more seeds | 30 distinct cases at one seed carry more information than 10 cases at three | 2 days and 3 CPU-hours | Now |
| Case-clustered bootstrap for every pooled rate | The pooled Wilson interval treats 3 seeds of 10 cases as 30 draws | 0.5 day | Now |
| A rule baseline next to every model metric | The deterministic critic scores 15/15 where the model scores 35/45; a reader should see both in one row | 0.5 day | Now |
| Trajectory metrics and a model-plan versus canonical-plan counterfactual | Makes "agentic" testable: what share of executed steps did the model originate, and does the model's plan beat the template? | 3 days, replay only | Now |
| Deterministic support check for citations, then an NLI scorer | Turns "the quote exists" into "the passage supports the claim" | 1 day now; 2-3 days with the corpus | Now, then with EDGAR |
| Human labels for the judge | The earlier plan (B5) promised 50 human labels. The owner labelling 50 items blind is free and is the only way the kappa means anything | 0.5 owner-day | Owner decision |
| Cassette manifest | Request text, prompt id and version, commit, Ollama version and model digest per recording | 1 day | Now |
| Automated prompt optimisation | 10 planner cases and 4 reporter cases: any search overfits | n/a | Not now; revisit at five times the case count |

## 5. What to remove or relabel

- The golden-slice row and every sentence built on it (`README.md:19, 160`, `CHANGELOG.md:20, 67`,
  `docs/evaluation/local_model/README.md:44-47, 88-92`) until a recording that contains those calls
  exists.
- "The guardrail, not the model, is what keeps the report honest" (`docs/evaluation/local_model/README.md:52-53`):
  the model proposed no untraceable number in these runs; the dropped claims failed on key paths and
  id namespaces (AI-1-03).
- "Pinned by digest" wherever it appears, until the digest is compared at call time.
- "Resumable approvals" and "resume after a crash" in `docs/RUNBOOK.md:28-31` and
  `COOKBOOK.md:960-962`, until a resume entry point exists.
- "OpenTelemetry-ready" in `README.md:42`, until a span is exported by a test.
- Judge "kappa 1.00" as a headline. Say what it is: agreement on five templates whose overclaim
  wording is quoted in the rubric.

## 6. Order of work

1. **0.10.0, truth first.** Retract or re-measure the wrong numbers; add the provenance guard and the
   CI replay gate; fix the validator; wire or delete the unwired pieces; close the two open edges
   (standalone MCP server, ingestion screen). Nothing here needs a new idea.
2. **0.11.0, capability with measurement.** Planner skills, thin graph with resume, EDGAR corpus with
   an independent query set, groundedness, injection classifier, trajectory metrics, challenger
   re-run on the fixed path.
3. **0.12.0, depth.** Paired re-record gate, memory A/B, generated red-team attacks, volatility
   foundation-model study, a more credible execution simulator.

The item IDs, effort, acceptance tests and lanes are in `PLAN_0.10.0.md`.
