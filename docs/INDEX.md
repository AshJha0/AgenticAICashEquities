# Documentation index

Landing site: https://ashjha0.github.io/AgenticAICashEquities/ (served from this folder,
see [GITHUB_PAGES.md](GITHUB_PAGES.md)).

| Document | What it is for |
|---|---|
| [../README.md](../README.md) | overview, quick start, layout, configuration |
| [../LEARN.md](../LEARN.md) | guided tour: concepts, how the repo implements them, real numbers, knowledge check |
| [../COOKBOOK.md](../COOKBOOK.md) | 43 copy-pasteable recipes |
| [SPECIFICATION.md](SPECIFICATION.md) | the original 0.3 specification (historical), with realised / partial / roadmap status as of 0.3.x and pointers to the current docs |
| [architecture/overview.md](architecture/overview.md) | components, data flow, design decisions, extension points |
| [ai-agents/ai-agents.md](ai-agents/ai-agents.md) | how LLM/AI/ML/agents actually work: the boundary, the agent roster, harness-owned governance, real-model findings, what is *not* here |
| [quant/quant.md](quant/quant.md) | the deterministic quant layer: TCA + cause attribution, Stage 2 signal-research maths, synthetic data, what is *not* here |
| [quant/real-data-and-paper.md](quant/real-data-and-paper.md) | 0.7.0 and 0.7.1 numbers: R5 real-data study (VOID) and RP paper trading (synthetic); kept out of the RAG corpus |
| [DIAGRAMS.md](DIAGRAMS.md) | twelve Mermaid diagrams: pipeline, state machine, tool-call path, evidence model, MCP topology, sequence, attribution, package dependencies, stage 2, CI, LLM/RAG/ML/evaluation, paper trading and the R5 real-data path |
| [threat-model/threat-model.md](threat-model/threat-model.md) | assets, trust boundaries, threats → controls → tests |
| [evaluation/evaluation.md](evaluation/evaluation.md) | scenarios, synthetic market model, attribution thresholds, metrics, the evaluation report, golden set, history and drift monitors |
| [ml/README.md](ml/README.md) | deterministic ML (change points, ridge combination, calibration), model cards, all fitted to synthetic data |
| [research/README.md](research/README.md) | the R5 real-data momentum study: preregistration, point-in-time universe, loaders, costs, and the VOID result ([result_momentum_2026-11.md](research/result_momentum_2026-11.md)), plus the v2 Stooq attempt, also VOID ([result_momentum_2026-11-v2.md](research/result_momentum_2026-11-v2.md)) |
| [review/2026-11-07/PLAN.md](review/2026-11-07/PLAN.md) | the 0.4.0 to 0.7.0 release plan and owner decisions D1 to D22; paper trading plan in [RP_PLAN.md](review/2026-11-07/RP_PLAN.md) |
| [api/api.md](api/api.md) | endpoints, auth and roles, schemas, errors |
| [GITHUB_PAGES.md](GITHUB_PAGES.md) | how the site is published and how to keep its numbers honest |
| [../CHANGELOG.md](../CHANGELOG.md) | release notes |
