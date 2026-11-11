# Documentation index

Landing site: https://ashjha0.github.io/AgenticAICashEquities/ (served from this folder,
see [GITHUB_PAGES.md](GITHUB_PAGES.md)).

| Document | What it is for |
|---|---|
| [../README.md](../README.md) | front page: what it is, quick start, real versus synthetic, results with their evidence |
| [../LEARN.md](../LEARN.md) | guided tour: concepts, how the repo implements them, real numbers, knowledge check |
| [learn/ai/README.md](learn/ai/README.md) | AI concepts tutorial track for students: LLMs, agents and the harness, guardrails, RAG, classical ML, honest evaluation, local models; each lesson has an offline exercise |
| [../COOKBOOK.md](../COOKBOOK.md) | 53 copy-pasteable recipes (44-48: the 0.8.0 execution package; 49-53: 0.9.0 local model eval, critic checks, hybrid retrieval, E1, durable runs) |
| [configuration.md](configuration.md) | every environment variable and every CI check (moved from the README in 0.9.0) |
| [RUNBOOK.md](RUNBOOK.md) | operations: restart, stuck task or approval, event-loop lag, audit chain break, disk full, API keys, replay gate (0.9.0) |
| [SPECIFICATION.md](SPECIFICATION.md) | the original 0.3 specification (historical), with realised / partial / roadmap status as of 0.3.x and pointers to the current docs |
| [architecture/overview.md](architecture/overview.md) | components, data flow, design decisions, extension points; system diagram, lifecycles, repository layout and MCP tool list (section 7) |
| [ai-agents/ai-agents.md](ai-agents/ai-agents.md) | how LLM/AI/ML/agents actually work (0.9.0: local open-weight model, critic statement checks, hybrid retrieval; details in [rag-hybrid-0.9.0.md](ai-agents/rag-hybrid-0.9.0.md)): the boundary, the agent roster, harness-owned governance, real-model findings, what is *not* here |
| [quant/quant.md](quant/quant.md) | the deterministic quant layer: TCA + cause attribution, Stage 2 signal-research maths, synthetic data, what is *not* here |
| [quant/real-data-and-paper.md](quant/real-data-and-paper.md) | 0.7.0 and 0.7.1 numbers: R5 real-data study (VOID) and RP paper trading (synthetic), plus the 0.8.0 research-realism and paper-control notes and the paper-trading and forward-log section moved from the README in 0.9.0; kept out of the RAG corpus |
| [DIAGRAMS.md](DIAGRAMS.md) | eighteen Mermaid diagrams: pipeline, state machine, tool-call path, evidence model, MCP topology, sequence, attribution, package dependencies, stage 2, CI, LLM/RAG/ML/evaluation, paper trading and the R5 real-data path, and (0.8.0) the policy and approval flow, the audit chain anchor and the execution package, and (0.9.0) the critic statement checks, local model evaluation and replay, and the harness mixins with the run journal |
| [threat-model/threat-model.md](threat-model/threat-model.md) | assets, trust boundaries, threats → controls → tests |
| [threat-model/owasp-llm-top10.md](threat-model/owasp-llm-top10.md) | OWASP LLM Top 10 (LLM01-LLM10) mapped to controls, code and tests (0.8.0) |
| [execution/execution.md](execution/execution.md) | the `ceap.execution` package (0.8.0): order book simulator, propagator impact, schedulers including Almgren-Chriss, child orders, auctions, markouts, predicted-vs-realised cost loop, regression attribution, trading rules; synthetic only |
| [evaluation/evaluation.md](evaluation/evaluation.md) | scenario catalogue (execution and research), scenarios, synthetic market model, attribution thresholds, metrics, the evaluation report, golden set, history and drift monitors; 0.8.0 per-agent evals, metric sensitivity, OOD golden split, 159-query retrieval eval, corpus governance |
| [ml/README.md](ml/README.md) | deterministic ML (change points, ridge combination with an equal-weight baseline, calibration), model cards and the model inventory, all fitted to synthetic data |
| [research/README.md](research/README.md) | research index: R5, E1 (49-industry momentum, NULL; [research/e1/README.md](research/e1/README.md)) and E2 ([why R5 could not pass](research/e2_why_r5_could_not_pass.md)); the R5 real-data momentum study: preregistration, point-in-time universe, loaders, costs, and the VOID result ([result_momentum_2026-11.md](research/result_momentum_2026-11.md)), plus the v2 Stooq attempt, also VOID ([result_momentum_2026-11-v2.md](research/result_momentum_2026-11-v2.md)); the 0.8.0 research realism toolkit (calibration, a-priori hurdle, INCONCLUSIVE, adversarial power curves, leakage canary, PBO, risk toolkit) |
| [review/2026-11-09/PLAN.md](review/2026-11-09/PLAN.md) | the 0.8.0 review and the 0.8.0/0.9.0 plan (workstreams A-I, lanes L1-L6) with per-step status |
| [review/2026-11-07/PLAN.md](review/2026-11-07/PLAN.md) | the 0.4.0 to 0.7.0 release plan and owner decisions D1 to D22; paper trading plan in [RP_PLAN.md](review/2026-11-07/RP_PLAN.md) |
| [api/api.md](api/api.md) | endpoints, auth and roles, schemas, errors; [api/openapi.json](api/openapi.json) is the generated OpenAPI document (checked in, diffed in CI) |
| [adr/README.md](adr/README.md) | architecture decision records 0001-0005 (0.9.0) |
| [testing/test-pyramid.md](testing/test-pyramid.md) | test layers, where they live, when they run; property, metamorphic and mutation testing (0.9.0) |
| [glossary.md](glossary.md) | execution, research and platform terms, each tied to the code that computes it (0.9.0) |
| [how-to-extend.md](how-to-extend.md) | add a signal, an MCP tool or a policy rule (0.9.0) |
| [exercises.md](exercises.md) | graded exercises for LEARN.md, with answers or checks (0.9.0) |
| [GITHUB_PAGES.md](GITHUB_PAGES.md) | how the site is published and how to keep its numbers honest |
| [../CHANGELOG.md](../CHANGELOG.md) | release notes |
