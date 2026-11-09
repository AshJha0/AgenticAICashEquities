# Architecture decision records

An ADR records one decision that shapes the code: the context that forced it, what was decided,
and what it costs. They are short on purpose. When a decision changes, a new ADR supersedes the old
one; the old one is kept and marked, so the history of why stays readable.

Format (after Michael Nygard, "Documenting Architecture Decisions", 2011): Status, Context,
Decision, Consequences, Evidence. "Evidence" names the code and tests that enforce the decision, so
a reader can check it rather than trust it.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-llm-never-computes.md) | The LLM never computes a number and never invents an evidence id | Accepted 0.1.0, recorded 0.9.0 |
| [0002](0002-policy-engine-is-the-authority.md) | The policy engine, not the prompt or the plan, decides what a tool call may do | Accepted 0.1.0, recorded 0.9.0 |
| [0003](0003-hashed-universal-lock.md) | One universal, hashed dependency lock, regenerated and never edited | Accepted 0.6.0, recorded 0.9.0 |
| [0004](0004-one-mcp-server-per-domain.md) | One MCP server per capability domain | Accepted 0.3.0, recorded 0.9.0 |
| [0005](0005-void-studies-publish-no-statistic.md) | A study that fails a preregistered gate is VOID and publishes no statistic | Accepted 0.7.0, recorded 0.9.0 |

The decisions were taken in earlier releases (column "Status"); 0.9.0 writes them down (plan item I1).
