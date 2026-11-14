# Architecture diagrams

Every diagram on this page renders natively on GitHub (Mermaid). Names, states, tool counts
and step ids are the repository's actual values — if you rename a module, update the diagram.

Contents: 1. [End-to-end investigation pipeline](#1-end-to-end-investigation-pipeline) ·
2. [Harness state machine](#2-harness-state-machine) · 3. [The path of one tool call](#3-the-path-of-one-tool-call) ·
4. [Evidence and finding model](#4-evidence-and-finding-model) · 5. [MCP topology](#5-mcp-topology) ·
6. [Investigation sequence](#6-investigation-sequence) · 7. [Attribution decision flow](#7-attribution-decision-flow) ·
8. [Package dependencies](#8-package-dependencies) · 9. [Stage 2 extension](#9-stage-2-research-workflow-realised-in-030) ·
10. [CI and the release gate](#10-ci-and-the-release-gate) · 11. [LLM, RAG, ML and evaluation (0.6.0)](#11-llm-rag-ml-and-evaluation-060) ·
12. [Paper trading and the R5 real-data path (0.7.0)](#12-paper-trading-and-the-r5-real-data-path-070) ·
13. [Policy and approval flow (0.8.0)](#13-policy-and-approval-flow-080) ·
14. [Audit chain and external anchor (0.8.0)](#14-audit-chain-and-external-anchor-080) ·
15. [Execution package (0.8.0)](#15-execution-package-080) ·
16. [Critic statement checks (0.9.0)](#16-critic-statement-checks-090) ·
17. [Local model evaluation and replay (0.9.0)](#17-local-model-evaluation-and-replay-090) ·
18. [Harness mixins and the run journal (0.9.0)](#18-harness-mixins-and-the-run-journal-090) ·
19. [Hybrid retrieval and the judge gate (0.9.0)](#19-hybrid-retrieval-and-the-judge-gate-090) ·
20. [Study E1 (0.9.0)](#20-study-e1-090)

## 1. End-to-end investigation pipeline

Solid arrows are data flow. The LLM appears only where reasoning happens (planning,
critique, narrative); every number on the path comes from `ceap.analytics`.

```mermaid
flowchart TD
    U["User<br/>trader / quant / dev"] --> API["API / CLI<br/>FastAPI · API keys · RBAC<br/>ceap.api · ceap.cli"]
    API --> PARSE["Understand request<br/>symbol + window parsed deterministically<br/>validated against dataset coverage"]
    PARSE --> H

    subgraph H["AGENT HARNESS — ceap.harness (control plane)"]
        MEM[("Case memory (D10)<br/>harness-written · human decisions only<br/>per dataset and owner · 90-day expiry<br/>OFF in evaluation")]
        PLAN["Planner agent<br/>structured plan: agents · optional tools · baseline · ranked hypotheses<br/>inputs in data blocks · canonical fallback"]
        VAL["Plan validation<br/>data steps for the chosen agents · governance tail appended<br/>windows pinned · policy pre-check"]
        EXEC["Step execution<br/>TOOL_CALL batches (asyncio.TaskGroup) · AGENT_CALL<br/>one bounded re-plan on failure"]
        CRIT["Critic agent<br/>evidence resolves · corroboration · contradictions<br/>model may only name a dispute check"]
        DISP["Dispute checks<br/>alternative baseline / horizon via the policy path<br/>a contradiction caps confidence (max 3)"]
        EV["Evidence validation<br/>unresolved ids · cited value at path · derived_from"]
        HYP["Hypothesis table<br/>pre-registered deterministic tests<br/>supported · refuted · untested"]
        SIGN["Optional human sign-off<br/>review payload · reason audited"]
        FIN["Reporter (two calls)<br/>claims validated vs facts → prose with citations<br/>quote-span audit · number + evidence-id audits"]
        MEM -. "3 nearest cases, read-only" .-> PLAN
        PLAN --> VAL --> EXEC --> CRIT --> DISP --> EV --> HYP --> SIGN --> FIN
        SIGN -. "case record" .-> MEM
    end

    EXEC --> MA["Market agent"] & XA["Execution agent"] & QA["Quant agent<br/>attribute_causes"] & RA["Risk agent"] & EA["Engineering agent"]
    MA & XA & QA & RA & EA -. "read plan-fetched data (fetched)<br/>typed AgentMessage envelopes" .-> EXEC
    EXEC --> REG["ToolRegistry<br/>MCPToolAdapter → MCPClient"]
    DISP --> REG

    REG --> MD["market_data MCP<br/>8 tools"] & EX["execution MCP<br/>8 tools · TCA inside"] & RK["risk MCP<br/>7 tools"] & EN["engineering MCP<br/>5 tools"] & KN["knowledge MCP<br/>3 tools · RAG"]
    MD & EX --> AN["Quant analytics — ceap.analytics<br/>VWAP · TWAP · IS · slippage · spread · impact<br/>volatility · liquidity · market statistics"]
    MD & EX & RK & EN --> DS["Synthetic dataset — ceap.data<br/>seeded · 50 scenarios · baseline + window"]
    KN --> KB["Knowledge base (R3C)<br/>10 versioned docs · 65 section chunks · BM25<br/>query built by code · role-filtered"]

    FIN --> REP["InvestigationReport<br/>findings · evidence · attribution · critique · trace"]
    REP --> API
```

## 2. Harness state machine

`ceap/harness/state_machine.py` — `TRANSITIONS` is the entire legal graph; anything else
raises `InvalidTransition`. `FAILED` and `CANCELLED` are reachable from every non-terminal
state.

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> PLANNING : task accepted
    PLANNING --> VALIDATING_PLAN : plan produced
    VALIDATING_PLAN --> EXECUTING : plan validated (+ governance steps)
    EXECUTING --> AWAITING_APPROVAL : tool requires human approval
    AWAITING_APPROVAL --> EXECUTING : approval decided
    EXECUTING --> CRITIQUING : critic step
    CRITIQUING --> EXECUTING : critique complete
    CRITIQUING --> VALIDATING_EVIDENCE : critic run by the harness
    EXECUTING --> VALIDATING_EVIDENCE : validation step
    VALIDATING_EVIDENCE --> EXECUTING : evidence validated
    VALIDATING_EVIDENCE --> FINALISING : straight to finalise
    EXECUTING --> FINALISING : finalise step
    FINALISING --> COMPLETED : report produced
    COMPLETED --> [*]
    CREATED --> FAILED
    PLANNING --> FAILED
    VALIDATING_PLAN --> FAILED : PlanValidationError
    EXECUTING --> FAILED
    EXECUTING --> CANCELLED : CancellationToken
    AWAITING_APPROVAL --> CANCELLED
    FAILED --> [*]
    CANCELLED --> [*]
```

## 3. The path of one tool call

`StepExecutor.execute_tool` is the only way a tool runs — for plan steps and for agents'
own calls alike (`HarnessToolInvoker`).

```mermaid
flowchart LR
    REQ["ToolRequest<br/>tool_id · arguments · correlation_id"] --> KNOWN{"tool in<br/>registry?"}
    KNOWN -- no --> ERR["ToolResult ERROR<br/>unknown tool"]
    KNOWN -- yes --> POL["RulePolicyEngine.evaluate<br/>denied-tools → untrusted-server → agent-allowlist → read-only<br/>→ capabilities → argument-guard → input-schema<br/>→ symbol-universe → knowledge-classification → risk-level"]
    POL -- DENY --> DEN["ToolResult DENIED<br/>rule + reason in policy_log"]
    POL -- REQUIRE_APPROVAL --> APR["ApprovalGateway<br/>state → AWAITING_APPROVAL"]
    APR -- rejected / timeout --> DEN
    APR -- approved --> RUN
    POL -- ALLOW --> RUN["retry_async(policy)<br/>asyncio.wait_for(step timeout ∧ task deadline)"]
    RUN --> AD["MCPToolAdapter.execute<br/>MCPClient.invoke(server, tool, args)"]
    AD --> SRV["MCP server tool<br/>in-process or stdio"]
    SRV --> RES["ToolResult SUCCESS<br/>data + Evidence(tool_id, args, correlation_id, sha256 digest)"]
    RES --> MEM["InvestigationMemory.record_tool<br/>evidence added · ToolOutput indexed by tool_id + args"]
    RES --> TR["Tracer span tool:*<br/>metrics ceap_tool_calls_total"]
    RUN -- TimeoutError after retries --> TO["ToolResult TIMEOUT"]
```

## 4. Evidence and finding model

Every claim in a report is a `Finding`; every `Finding` cites `Evidence` ids that must resolve
in `InvestigationMemory` or the finding is dropped at `VALIDATING_EVIDENCE`.

```mermaid
classDiagram
    class Evidence {
        +id  "EXEC-3f2a9c1d"
        +type EvidenceType
        +source "execution.get_executions | agent:quant"
        +description
        +timestamp
        +attributes  arguments · correlation_id · digest
    }
    class EvidenceType {
        <<enumeration>>
        MARKET_DATA
        ORDER_DATA
        EXECUTION_DATA
        ORDER_BOOK
        SYSTEM_METRIC
        LOG
        DOCUMENT
        CALCULATION
        CODE_CHANGE
        RISK
    }
    class Finding {
        +id "F-8b11d0aa"
        +statement
        +supporting_evidence  tuple~str~
        +contradicting_evidence  tuple~str~
        +confidence  0..1
        +category  MARKET | EXECUTION | ATTRIBUTION | RISK | TECHNOLOGY
        +produced_by  agent id
        +attributes  anomaly · role · critic_notes
    }
    class InvestigationReport {
        +executive_summary
        +primary_observations
        +conclusion
        +alternative_explanations
        +attribution
        +critique  assessments · contradictions · narrative audits
        +narrative
    }
    class InvestigationMemory {
        +evidence  dict~id, Evidence~
        +findings  list~Finding~
        +tool_outputs  list~ToolOutput~
        +unresolved_evidence(finding)
    }
    Finding "1" --> "1..*" Evidence : cites
    InvestigationReport "1" --> "*" Finding
    InvestigationReport "1" --> "*" Evidence
    InvestigationMemory --> Evidence
    InvestigationMemory --> Finding
    Evidence --> EvidenceType
```

## 5. MCP topology

One `MCPServerDefinition` per server; the same object answers in-process calls and is exported
to a real `FastMCP` server over stdio. Tool schemas are derived from Python signatures, so the
two representations cannot drift.

```mermaid
flowchart LR
    subgraph DEF["ceap.mcp.server — MCPServerDefinition (x8)"]
        T["@server.tool(description, read_only, risk_level, required_capabilities)<br/>schema_from_signature()"]
    end
    DEF --> IP["InProcessMCPClient<br/>tests · CLI · API"]
    DEF --> FM["to_fastmcp() → FastMCP<br/>python -m ceap.mcp.&lt;server&gt;"]
    FM --> STDIO["stdio transport<br/>official mcp SDK"]
    STDIO --> SC["StdioMCPClient<br/>ClientSession per server"]
    IP & SC --> DISC["build_tool_registry<br/>discover_tools → MCPToolAdapter"]
    DISC --> REG["ToolRegistry<br/>39 tools: market_data.* · execution.* · risk.* · engineering.* · knowledge.*<br/>research_data.* · alpha.* · backtest.* (Stage 2) — all read-only except execution.stage_orders"]
    REG --> HARN["Harness executor<br/>policy reads readOnlyHint · riskLevel · requiredCapabilities"]
```

## 6. Investigation sequence

The canonical plan for the MVP question, as the deterministic planner emits it (a real model
produces the same shape; unknown tools are stripped).

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as API / CLI
    participant H as AgentHarness
    participant P as Planner (LLM)
    participant M as MCP servers
    participant S as Specialist agents
    participant C as Critic
    participant R as Reporter (LLM)

    U->>A: "Why did AAPL execution deteriorate between 14:00 and 15:00?"
    A->>A: parse symbol + window, validate dataset coverage
    A->>H: Task(input: symbol, window, baseline, dataset)
    H->>P: tool catalogue + parameters (question as data)
    P-->>H: JSON plan → sanitised Plan (15 TOOL_CALL, 6 AGENT_CALL, VALIDATION, FINALISE)
    H->>H: validate plan · policy pre-check · append governance steps
    par TOOL_CALL batch (TaskGroup)
        H->>M: get_parent_orders / get_child_orders / get_executions
        H->>M: get_market_statistics (window, baseline) · get_order_book_statistics (×2)
        H->>M: get_execution_metrics (window, baseline) · get_venue_statistics
        H->>M: get_latency_metrics · get_deployments · search_logs
        H->>M: get_strategy_configuration · knowledge.search_documents
    end
    M-->>H: ToolResults + Evidence (digests) → InvestigationMemory
    H->>S: Market → Execution → Engineering → Risk → Quant
    S-->>H: Findings (+ CALCULATION evidence, attribution)
    H->>C: findings + evidence + agent outputs   (state: CRITIQUING)
    C-->>H: adjusted findings, contradictions, unsupported ids
    H->>H: VALIDATING_EVIDENCE — drop unresolved findings
    H->>R: structured facts (metrics, attribution, findings, critique, policy context)
    R-->>H: narrative → number audit + evidence-id audit
    H-->>A: HarnessResult (report, plan, evidence, policy log, trace, state history)
    A-->>U: report
```

## 7. Attribution decision flow

`ceap.analytics.attribution.attribute_causes` — thresholds in `Thresholds`, materiality at
0.35. The two guards on the right are the ones the evaluation suite forced into existence.

```mermaid
flowchart TD
    IN["window ExecutionMetrics + MarketStatistics<br/>baseline ExecutionMetrics + MarketStatistics<br/>engineering summary"] --> D["deteriorated?<br/>IS residual z ≥ 1.96 vs pre-trade estimate<br/>(fallback: ΔIS or ΔVWAP-slippage ≥ 1.5 bps)"]
    IN --> V["MARKET_VOLATILITY<br/>vol ratio > 1.6"]
    IN --> S["WIDE_SPREADS<br/>spread ratio > 1.4"]
    IN --> L["LOW_LIQUIDITY<br/>min(depth, TOB) ratio < 0.65"]
    IN --> VN["VENUE_DEGRADATION<br/>fill gap > 0.25 · slip excess > 2 bps · reject excess"]
    IN --> T["TECHNOLOGY_LATENCY<br/>latency ratio > 3 · reject rate > 5%"]
    IN --> MDQ["MARKET_DATA_ANOMALY<br/>stale > 3% (and 3× baseline) · crossed > 5"]
    IN --> LO["LARGE_ORDER_IMPACT<br/>participation > 20% (+0.3 if parent ≥ 4×)"]
    IN --> PM["PRICE_MOVEMENT<br/>adverse drift > 60 bps"]
    T -. "platform reject rate > 5% ⇒<br/>venue reject signal disabled" .-> VN
    V -. "vol ratio > 1.6 ⇒<br/>price-move score halved" .-> PM
    V & S & L & VN & T & MDQ & LO & PM --> SC["score = 1 − exp(−excess / scale)"]
    SC --> RK["rank · material if score ≥ 0.35"]
    RK --> OUT{"any material?"}
    OUT -- yes --> PRI["primary = top · secondary = rest<br/>CALCULATION evidence with full table"]
    OUT -- no --> NRM["primary = NORMAL"]
    D --> PRI
```

## 8. Package dependencies

Arrows point from a package to what it imports at module level (imports inside functions, used to
break cycles, are not shown). Checked against the source for 0.9.0 with
`grep -rhoE '^(from|import) ceap\.[a-z_]+' src/ceap/<package>`. `domain` imports only the
dependency-free leaf `ceap.util`; the LLM SDK, MCP SDK and FastAPI are each confined to one
package. Two edges are less clean than the layering suggests and are shown as they are:
`analytics.tca_aggregate` imports `ceap.data`, `ceap.ml` and `ceap.policy` (the monthly TCA report
uses the pre-trade model and runs its own approval step), and `rag.hybrid_eval` imports `ceap.eval.stats`.

```mermaid
flowchart BT
    util["ceap.util<br/>(stdlib only)"]
    domain["ceap.domain<br/>(imports only ceap.util)"] --> util
    data["ceap.data"] --> domain & analytics
    analytics["ceap.analytics"] --> domain & util
    analytics -. "tca_aggregate" .-> data & policy & ml["ceap.ml"]
    ml --> analytics & util
    rag["ceap.rag"] -. "hybrid_eval" .-> eval
    policy["ceap.policy"] --> domain & rag & util
    obs["ceap.observability"] --> domain & util
    llm["ceap.llm<br/>(anthropic SDK; Ollama over HTTP)"] --> config["ceap.config"] & domain & util
    persistence["ceap.persistence<br/>(SQLite)"] --> domain & util
    execution["ceap.execution (0.8.0)"] --> analytics & data & domain
    mcp["ceap.mcp<br/>(mcp SDK confined here)"] --> domain & data & analytics & rag & paper & util
    paper["ceap.paper"] --> analytics & data & execution & persistence & policy & util
    agents["ceap.agents"] --> domain & analytics & data & harness & llm & ml & rag & eval
    harness["ceap.harness"] --> domain & policy & obs & config & llm & mcp & agents & analytics & util
    eval["ceap.eval (0.9.0 local model)"] --> agents & analytics & data & llm & rag
    studies["ceap.studies (E1)"] --> data
    platform["ceap.platform"] --> agents & harness & mcp & llm & policy & rag & data & persistence & obs
    api["ceap.api<br/>(FastAPI confined here)"] --> platform
    cli["ceap.cli"] --> platform
    evaluation["ceap.evaluation"] --> platform
```

## 9. Stage 2: research workflow (realised in 0.3.0)

The research workflow runs on the same harness, policy engine, approval gateway and evidence
model. Tool calls come first (their outputs are cached evidence), the agents interpret them, the
critic computes the deterministic assessment, and the harness owns the governance tail: proposal
approval, then - only when requested and only for a principal with `trading:execute` - a second
approval and the single non-read-only tool, `execution.stage_orders` (paper orders).

```mermaid
flowchart LR
    RD["research_data<br/>universe · daily bars · regime"] --> RA["Research agent<br/>hypothesis · coverage"]
    AL["alpha.evaluate_signal<br/>rank IC · t-stat · turnover · decay"] --> AA["Alpha agent<br/>ALPHA / NO_ALPHA / ROBUST"]
    BE["backtest.run_backtest<br/>event-driven · cost model · walk-forward"] --> BA["Backtest agent<br/>PROFITABLE / OOS_DECAY / COST_DRAG"]
    RK["risk.*portfolio*<br/>recomputed target · limits · stress"] --> PR["Portfolio risk agent<br/>LIMIT_BREACH / STRESS_LOSS"]
    RA --> CR
    AA --> CR
    BA --> CR
    PR --> CR["Critic<br/>assess_research → verdict + flags<br/>caps contradicted claims"]
    CR --> VA["VALIDATION<br/>every finding resolves its evidence"]
    VA --> HA["HUMAN_APPROVAL gov-approval<br/>sees the assessment + target portfolio"]
    HA -- declined --> FAILED["FAILED<br/>no report"]
    HA -- approved --> SA{"stage_orders<br/>requested?"}
    SA -- no --> FIN["FINALISE<br/>research proposal"]
    SA -- yes --> HB["HUMAN_APPROVAL gov-stage-approval<br/>sees the order preview"]
    HB -- declined --> FIN
    HB -- approved --> SO["execution.stage_orders<br/>HIGH · trading:execute · paper"]
    SO --> FIN
```

## 10. CI and the release gate

Added in 0.4.0 (Round R0). Every PR runs the jobs on the left; a release happens only from a tag on
a commit whose checks are all green, and the gate re-checks versions, CHANGELOG, banned words and
licences before anything is built.

```mermaid
flowchart LR
    PR["pull request"] --> L["lint<br/>ruff · mypy · banned words<br/>check_docs.py · drift monitors"]
    PR --> W["test-windows"]
    PR --> BM["benchmarks gate"]
    PR --> AR["api-reference<br/>checked-in OpenAPI"]
    PR --> T["test 3.11 / 3.12<br/>xdist · branch coverage >= 85 %<br/>pytest-timeout 120/600/1800 s"]
    PR --> LF["lock-fresh<br/>uv --universal --generate-hashes"]
    PR --> A["audit<br/>pip-audit"]
    PR --> S["secrets<br/>gitleaks + planted-key self-test"]
    PR --> D["docker<br/>build · ceap --help"]
    PR --> C["changelog<br/>or label no-changelog"]
    PR --> K["contracts<br/>or label contracts-change"]
    PR --> GC["golden-change<br/>label + owner sign-off"]
    L & T & W & BM & AR & LF & A & S & D & C & K & GC --> M["merge to main<br/>CI re-runs on main"]
    M --> TAG["tag vX.Y.Z"]
    TAG --> G{"release_check.py<br/>latest CI workflow run green? versions agree?<br/>CHANGELOG section? banned words? licences?"}
    G -- no --> X["no release"]
    G -- yes --> P["build wheel, sdist, image<br/>gh release create"]
    N["nightly.yml<br/>all tiers · random order · 1000 examples<br/>mutation ratchet · API fuzz"] -.-> M
```

## 11. LLM, RAG, ML and evaluation (0.6.0)

Rounds R3A, R3B, R3C, R2B and R3E. The model proposes; code validates, computes and audits. Every
model-dependent path is exercised offline (mock client, cassettes, wire contract tests); no live
model call was made (D15), so live quality is UNKNOWN / NOT RUN LIVE.

```mermaid
flowchart TD
    subgraph LLM["LLM layer — ceap.llm (R3A)"]
        PIN["model_pins.toml<br/>one pinned model per purpose"]
        PRM["prompts/*.md<br/>versioned: planner@3 · critic@3 ·<br/>reporter_claims@1 · reporter_prose@1"]
        CL["AnthropicLLMClient / MockLLMClient<br/>strict schemas · messages.parse() · cache_control"]
        CAS[("cassettes<br/>scrubbed recorded wire traffic")]
        PIN & PRM --> CL
        CAS -. "replay in tests" .-> CL
    end
    subgraph AG["Agents within the boundary (R3B)"]
        PL["Planner<br/>enumerated scope · ranked hypotheses"]
        CR["Critic<br/>dispute only via a named check"]
        RP["Reporter<br/>claims call → prose call with citations"]
    end
    subgraph RAG["Retrieval — ceap.rag (R3C)"]
        Q["retrieval_query<br/>built by code, not the model"]
        BM["BM25 over 65 chunks<br/>10 versioned, classified docs"]
        POL["policy: knowledge-classification<br/>role-filtered"]
        Q --> POL --> BM
    end
    subgraph ML["ML — deterministic, synthetic-labelled (R2B)"]
        CP["CUSUM change points"]
        RG["ridge signal combination<br/>purged CV · ledger trials"]
        CAL["isotonic calibration<br/>held-out Brier / ECE"]
        REG[("model registry<br/>load by content hash")]
        CP & RG & CAL --> REG
    end
    subgraph EV["Evaluation (R3E)"]
        GS[("golden set v1.0.0<br/>30 + 15 items · items hash")]
        ER["ceap evaluation-report<br/>capability · safety · robustness · cost · live-only"]
        OUT[("latest.json · history.csv")]
        DR["drift monitors<br/>cassettes · RAG floors · golden · pins · SDK"]
        CD["check_docs.py<br/>doc numbers = latest.json"]
        GS --> ER --> OUT --> CD
    end
    CL --> PL & CR & RP
    PL --> Q
    BM -- "DOCUMENT evidence" --> RP
    RP -- "quote-span audit → DOC evidence" --> REPT["InvestigationReport"]
    REG -- "CALCULATION evidence" --> REPT
    REPT --> ER
    CAS & BM & GS --> DR
```

## 12. Paper trading and the R5 real-data path (0.7.0)

Today only the synthetic branch runs, daily since the owner enabled the scheduled job (forward
record from 2026-11-09). The Stooq branch is written but disabled: `config/paper.json` stays on the
synthetic feed, no Stooq data is in the repository, and the feed refuses every row up to 2025-12-31
so the R5 pseudo-holdout stays unspent. The R5 study stopped at its coverage gate twice (VOID; the
0.7.1 v2 attempt on Stooq also failed it).

```mermaid
flowchart TD
    subgraph R5["R5 real-data study (VOID)"]
        PRE["preregistration<br/>prereg_lock.json (SHA-256)"] --> UNI["universe_pit<br/>point-in-time S&P 500"]
        UNI --> LOAD["yfinance / Stooq loaders<br/>data/real_cache/ (gitignored)"]
        LOAD --> QG["quality gate"]
        QG --> G1{"gate 1: coverage >= 95 %?"}
        G1 -- "75.1 %: no" --> VOID["VOID: no statistic,<br/>holdout 2024-2025 UNSPENT"]
    end
    subgraph RP["RP paper trading"]
        SYN["feed: synthetic<br/>(config/paper.json, enabled)"] --> FEED["DailyFeed"]
        STQ["feed: stooq<br/>ceap.paper.feeds_real (disabled)"] -. "refuses rows <= 2025-12-31" .-> FEED
        FEED --> SIG["signal on rows <= D"] --> TGT["target -> deltas"]
        TGT --> PTC["pre-trade checks<br/>restricted, limits, 25 % ADV, price band"]
        PTC --> APR["QueuedApprovalGateway<br/>paper-auto, audited"]
        APR --> FILL["simulate_day(D+1)<br/>next-close fills, 10 % ADV cap"]
        HALT["CEAP_PAPER_HALT or<br/>ceap db kill-switch (0.8.0)"] -. "blocks new orders" .-> PTC
        FILL --> LED["paper_ledger<br/>HMAC chain (0.8.0), append-only"]
        LED --> REC{"end-of-day reconciliation"}
        REC -- "break: exit 4, nothing committed" --> BRK["break report"]
        REC -- "clean" --> LOG["forward log / simulated log<br/>report, export CSV"]
    end
    LOAD -. "same cache, after the holdout only" .-> STQ
```

## 13. Policy and approval flow (0.8.0)

`RulePolicyEngine` runs its rules in this order (`src/ceap/policy/engine.py`); the first DENY wins, then
REQUIRE_APPROVAL, otherwise ALLOW. Since 0.8.0 a HIGH-risk tool always needs a human approval, an
admin cannot decide one, and four-eyes compares named persons (API keys mapped with
`subject=NAME`), not keys. The gateway re-checks the content hash of the exact arguments after the
decision.

```mermaid
flowchart TD
    REQ["ToolRequest<br/>(agent, principal, tool, arguments)"] --> R1["denied tools"]
    R1 --> R2["untrusted MCP server?<br/>outside CEAP_MCP_TRUSTED_SERVERS: DENY"]
    R2 --> R3["per-agent allowlist<br/>ceap.policy.agents ∩ principal"]
    R3 --> R4["read-only / tools:write"]
    R4 --> R5["capabilities"]
    R5 --> R6["argument guards<br/>length, items, depth, whole-number limit"]
    R6 --> R7["JSON schema of the tool"]
    R7 --> R8["symbol universe<br/>every symbol-bearing field, x-ceap-symbol;<br/>no universe: DENY"]
    R8 --> R9["knowledge classification"]
    R9 --> R10{"risk level"}
    R10 -- "CRITICAL" --> DENY["DENY"]
    R10 -- "HIGH (always)" --> AP["REQUIRE_APPROVAL"]
    R10 -- "MEDIUM without risk:medium" --> AP
    R10 -- "LOW / READ" --> ALLOW["ALLOW"]
    AP --> GW["approval gateway<br/>GET /approvals/{id}: tool, arguments, content_hash"]
    GW --> FE{"approver is a different person,<br/>role approver (not admin)?"}
    FE -- "no" --> DENY
    FE -- "yes" --> HC{"content hash unchanged?"}
    HC -- "no: 409 / refused" --> DENY
    HC -- "yes" --> RUN["tool runs; decision in the audit log"]
```

## 14. Audit chain and external anchor (0.8.0)

Each audit row and paper-ledger row links to the previous one with HMAC-SHA256 under
`CEAP_AUDIT_HMAC_KEY`, which never lives in the database. Rows written before migration 0004 stay
plain SHA-256 as a sealed legacy prefix. An anchor file committed to git (optionally with an
OpenTimestamps proof of its `.sha256` sidecar) lets `verify-chain` catch a rewritten or truncated
chain even by someone who holds the key.

```mermaid
flowchart LR
    EV["audit event<br/>(redacted payload)"] --> ROW["row n<br/>hash_alg, prev_hash"]
    KEY["CEAP_AUDIT_HMAC_KEY<br/>(+ _OLD_KEYS for rotation)"] -. "outside the DB" .-> ROW
    ROW --> CH["chain head n"]
    CH --> ANC["ceap db anchor<br/>heads + row counts -> file"]
    ANC --> GIT["commit to git<br/>+ .sha256 sidecar for OpenTimestamps"]
    GIT --> VER{"ceap db verify-chain --anchor FILE"}
    CH --> VER
    VER -- "head and row count match" --> OK["ok (dev_key flagged if no key set)"]
    VER -- "rewritten or cut-off tail" --> BAD["break reported"]
```

## 15. Execution package (0.8.0)

`ceap.execution` (guide: [execution/execution.md](execution/execution.md)), a simulator library, not used by the default paper book, runs a parent order
through a simulated limit order book. Everything here is synthetic; nothing routes a real order.

```mermaid
flowchart TD
    PARENT["parent order"] --> SCH["Schedule (schedule.py)<br/>TWAP · VWAP · POV · Almgren-Chriss<br/>whole-share allocation"]
    SCH --> CH["child-order logic (child.py)<br/>passive / aggressive, limits, cancel-replace"]
    CH --> RULES["rules.py<br/>tick, round lot, LULD"]
    RULES --> LOB["LOB simulator (lob.py)<br/>price-time FIFO, latency, icebergs,<br/>passive fill only when traded through"]
    AUC["auction uncross (auction.py)"] --> FILLS
    LOB --> FILLS["fills"]
    FILLS --> IMP["propagator impact (impact.py)<br/>driven by own fills; fit vs truth"]
    IMP -. "moves the price" .-> LOB
    FILLS --> MK["markouts by venue and flag (markouts.py)<br/>bootstrap CIs, routing table"]
    FILLS --> IS["implementation shortfall<br/>fill_rate, opportunity_priced"]
    IS --> LOOP["predicted vs realised (cost_loop.py)<br/>SQLite, weekly recalibration, drift alert"]
    IS --> ATT["cause attribution<br/>heuristic, or regression with CEAP_ATTRIBUTION=regression"]
```

## 16. Critic statement checks (0.9.0)

`ceap.agents.critic_checks.check_statement` runs on every finding after the evidence checks; each
flag caps confidence (`CHECK_CAPS` in `ceap/agents/critic.py`) and is recorded in
`assessment["flags"]`.

The three checks are independent: one finding can collect more than one flag.

```mermaid
flowchart TD
    F["finding: statement + cited evidence ids"] --> R["resolve cited records<br/>(supporting, contradicting)"]
    R --> C1{"cited records disagree<br/>or a contradicting record?"}
    C1 -- yes --> X1["contradicted<br/>cap 0.4"]
    R --> M["find the metric named<br/>(attribute name, or phrase table<br/>plus the attribute's other qualifiers)"]
    M --> S["direction word present?<br/>yes: *change* attributes<br/>no: level attributes"]
    S --> C2{"a printed number agrees<br/>(1 % / 0.005, within its printed<br/>rounding, or as a percent)?"}
    C2 -- no --> X2["numeric_mismatch<br/>cap 0.2"]
    S --> C3{"direction word vs sign of<br/>the cited change, given polarity"}
    C3 -- disagree --> X3["direction_mismatch<br/>cap 0.2"]
```

## 17. Local model evaluation and replay (0.9.0)

```mermaid
flowchart LR
    subgraph live ["live run (CEAP_LOCAL_MODEL=1, this machine)"]
        RUN["ceap.eval.local_model<br/>injection · judge · challenger"] --> OC["OllamaLLMClient<br/>qwen2.5:7b-instruct /<br/>llama3.1:8b challenger"]
        OC --> CAS["LocalCassette<br/>tests/cassettes/local_model/<br/>key = canonical request hash<br/>(checkpoint after every call)"]
    end
    subgraph ci ["CI (no Ollama)"]
        REP["--replay"] --> LCC["LocalCassetteClient"] --> SC["scores + Wilson CIs<br/>docs/evaluation/local_model/"]
    end
    CAS --> LCC
    OC --> SLO["AISLOMonitor<br/>plan-valid · repair · fallback ·<br/>untraceable numbers · p95 latency"]
```

## 18. Harness mixins and the run journal (0.9.0)

```mermaid
flowchart TD
    E["engine.py: AgentHarness<br/>(__init__, execute)"] --> P["planning.py"]
    E --> D["dispatch.py"]
    E --> A["approvals.py"]
    E --> V["evidence.py"]
    P & D & A & V --> B["base.py: HarnessBase (typed shared state)"]
    D -- "each tool call" --> K["idempotency key<br/>sha256(task | step | tool | args)"]
    K --> J[("run journal<br/>0005_run_journal.sql<br/>CEAP_DURABLE_RUNS=1")]
    J -- "re-run of same task id" --> D
    A -- "same content hash" --> J
```

The approval sweep (`ceap.api.loopmon.approval_sweeper`, interval `CEAP_APPROVAL_SWEEP_S`) expires
pending approvals older than `CEAP_APPROVAL_TIMEOUT_S`, so a restart cannot leave a step waiting
forever.

## 19. Hybrid retrieval and the judge gate (0.9.0)

Two gates fixed before their runs. Retrieval: BM25 is the default; `HybridRetriever` is opt-in
because the reranker needs a model at query time. Judge: a rubric dimension is used only when
Cohen's kappa against a hand-labelled reference exceeds 0.6 (`KAPPA_BAR` in `ceap/eval/judge.py`);
the committed calibration enables `hedged` only (`docs/evaluation/local_model/judge_calibration.json`).

```mermaid
flowchart LR
    subgraph RAG ["ceap.rag"]
        Q["query built by code"] --> BM["BM25 (default)"]
        Q -. "opt-in" .-> HY["HybridRetriever (dense.py)<br/>embeddings + rank fusion + reranker<br/>hashed model cache"]
        BM & HY --> AB{"min_support gate<br/>(abstain: no policy found)"}
        AB -- "supported" --> DOC["DOCUMENT evidence"]
        AB -- "below support" --> NONE["no citation"]
    end
    subgraph JG ["ceap.eval.judge"]
        REF[("judge_reference_v1<br/>hand labels, sha256")] --> CALI["calibrate(): kappa per dimension"]
        CALI --> GATE{"kappa > 0.6?"}
        GATE -- "yes" --> EN["dimension enabled"]
        GATE -- "no" --> OFF["dimension off; reported, not used"]
    end
```

## 20. Study E1 (0.9.0)

`ceap.studies.industry_momentum` refuses to run when the preregistration hash differs from
`prereg_lock.json`, and refuses a second holdout opening. Guide: [research/e1/README.md](research/e1/README.md).

```mermaid
flowchart LR
    PRE["prereg_industry_momentum_2026-11.md<br/>+ prereg_lock.json (committed first)"] --> LOAD["Ken French 49 industries<br/>data/real_cache/kf (git-ignored)<br/>raw SHA-256 in data_hashes.json"]
    LOAD --> IS["in-sample 1927-1989<br/>result_in_sample.json"]
    IS --> LED{"holdout_ledger.json<br/>already opened?"}
    LED -- "yes" --> REFUSE["refuse"]
    LED -- "no: open once" --> HO["holdout 1990-2015<br/>Newey-West t on net returns, 10 bps/side"]
    HO --> V["NULL: t 1.16 < 1.645<br/>result_holdout.json"]
    HO --> LIVE["live 2016 on: descriptive only"]
```
