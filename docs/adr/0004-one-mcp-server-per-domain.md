# ADR 0004: One MCP server per capability domain

- Status: Accepted (0.3.0). Recorded 0.9.0.

## Context

Agents reach capabilities through MCP tools. One large server would mix concerns with very
different risk: reading market data is harmless, staging orders is not. It would also make the tool
catalogue one blob whose change history is hard to review.

## Decision

Each capability domain has its own MCP server under `src/ceap/mcp/`: `market_data`, `execution`,
`risk`, `alpha`, `backtest`, `research_data`, `knowledge` and `engineering`.
`ceap.mcp.registry.build_default_servers` discovers them and wraps each tool in an
`MCPToolAdapter`, so the harness sees one domain `ToolRegistry`. Each server can also run on its
own over stdio (for example `python -m ceap.mcp.risk`).

## Consequences

- Risk attaches per domain: tool metadata (side effects, data classification) is declared next to
  the tool, and the policy engine (ADR 0002) reads it.
- The catalogue is hashed (`catalogue_hash`), so a change to any tool schema shows up in review.
- Eight servers mean eight small `server.py` files with shared boilerplate in `mcp/common.py`.

## Evidence

- `src/ceap/mcp/registry.py`, `src/ceap/mcp/adapter.py`.
- `tests/mcp/`.
