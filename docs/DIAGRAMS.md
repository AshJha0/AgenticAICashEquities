# Architecture diagrams

Every diagram on this page renders natively on GitHub (Mermaid). Names, states, tool counts
and step ids are the repository's actual values — if you rename a module, update the diagram.

Contents: 1. [End-to-end investigation pipeline](#1-end-to-end-investigation-pipeline) ·
2. [Harness state machine](#2-harness-state-machine) · 3. [The path of one tool call](#3-the-path-of-one-tool-call) ·
4. [Evidence and finding model](#4-evidence-and-finding-model) · 5. [MCP topology](#5-mcp-topology) ·
6. [Investigation sequence](#6-investigation-sequence) · 7. [Attribution decision flow](#7-attribution-decision-flow) ·
8. [Package dependencies](#8-package-dependencies) · 9. [Stage 2 extension](#9-stage-2-extension)

## 1. End-to-end investigation pipeline

Solid arrows are data flow. The LLM appears only where reasoning happens (planning,
critique, narrative); every number on the path comes from `ceap.analytics`.

```mermaid
flowchart TD
    U["User<br/>trader / quant / dev"] --> API["API / CLI<br/>FastAPI · API keys · RBAC<br/>ceap.api · ceap.cli"]
    API --> PARSE["Understand request<br/>symbol + window parsed deterministically<br/>validated against dataset coverage"]
    PARSE --> H

    subgraph H["AGENT HARNESS — ceap.harness (control plane)"]
        PLAN["Planner agent<br/>LLM proposes JSON plan → sanitised against tool catalogue<br/>canonical fallback"]
        VAL["Plan validation<br/>structure · unknown tools/agents · policy pre-check<br/>governance steps appended"]
        EXEC["Step execution<br/>TOOL_CALL batches (asyncio.TaskGroup) · AGENT_CALL"]
        CRIT["Critic agent<br/>evidence resolves · corroboration · contradictions<br/>LLM may only lower confidence"]
        EV["Evidence validation<br/>unresolved findings dropped"]
        FIN["Reporter<br/>facts → LLM narrative → number + evidence-id audits"]
        PLAN --> VAL --> EXEC --> CRIT --> EV --> FIN
    end

    EXEC --> MA["Market agent"] & XA["Execution agent"] & QA["Quant agent<br/>attribute_causes"] & RA["Risk agent"] & EA["Engineering agent"]
    MA & XA & QA & RA & EA --> REG["ToolRegistry<br/>MCPToolAdapter → MCPClient"]
    EXEC --> REG

    REG --> MD["market_data MCP<br/>8 tools"] & EX["execution MCP<br/>6 tools · TCA inside"] & RK["risk MCP<br/>4 tools"] & EN["engineering MCP<br/>5 tools"] & KN["knowledge MCP<br/>3 tools · RAG"]
    MD & EX --> AN["Quant analytics — ceap.analytics<br/>VWAP · TWAP · IS · slippage · spread · impact<br/>volatility · liquidity · market statistics"]
    MD & EX & RK & EN --> DS["Synthetic dataset — ceap.data<br/>seeded · 50 scenarios · baseline + window"]
    KN --> KB["Knowledge base<br/>8 docs · 34 chunks · hashed TF-IDF"]

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
    EXECUTING --> VALIDATING_EVIDENCE : validation step
    VALIDATING_EVIDENCE --> EXECUTING : evidence validated
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
    KNOWN -- yes --> POL["RulePolicyEngine.evaluate<br/>denied-tools → read-only → capabilities<br/>→ argument-guard → symbol-universe → risk-level"]
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
    subgraph DEF["ceap.mcp.server — MCPServerDefinition (x5)"]
        T["@server.tool(description, read_only, risk_level, required_capabilities)<br/>schema_from_signature()"]
    end
    DEF --> IP["InProcessMCPClient<br/>tests · CLI · API"]
    DEF --> FM["to_fastmcp() → FastMCP<br/>python -m ceap.mcp.&lt;server&gt;"]
    FM --> STDIO["stdio transport<br/>official mcp SDK"]
    STDIO --> SC["StdioMCPClient<br/>ClientSession per server"]
    IP & SC --> DISC["build_tool_registry<br/>discover_tools → MCPToolAdapter"]
    DISC --> REG["ToolRegistry<br/>26 tools: market_data.* · execution.* · risk.* · engineering.* · knowledge.*"]
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
    IN["window ExecutionMetrics + MarketStatistics<br/>baseline ExecutionMetrics + MarketStatistics<br/>engineering summary"] --> D["deteriorated?<br/>ΔIS ≥ 1.5 bps OR ΔVWAP-slippage ≥ 1.5 bps"]
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

Arrows point from a package to what it imports. `domain` imports nothing from the platform;
`analytics` and `data` depend only on `domain`; the LLM SDK, MCP SDK and FastAPI are each
confined to one package.

```mermaid
flowchart BT
    domain["ceap.domain<br/>(no platform imports)"]
    analytics["ceap.analytics"] --> domain
    data["ceap.data"] --> domain
    data --> analytics
    rag["ceap.rag"]
    mcp["ceap.mcp<br/>(mcp SDK confined here)"] --> domain & data & analytics & rag
    llm["ceap.llm<br/>(anthropic SDK confined here)"] --> config["ceap.config"]
    policy["ceap.policy"] --> domain
    obs["ceap.observability"]
    harness["ceap.harness"] --> domain & policy & obs & config
    agents["ceap.agents"] --> domain & analytics & harness & llm
    platform["ceap.platform"] --> agents & harness & mcp & llm & policy & rag & data
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
    BE["backtest.run_backtest<br/>event-driven · cost model · walk-forward"] --> BA["Backtest agent<br/>PROFITABLE / OVERFIT / COST_DRAG"]
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
