"""Cash Equities Agentic Platform (CEAP).

An enterprise-style Agentic AI platform that investigates, analyses and
explains cash-equity trading and market behaviour.

Architectural boundary (kept strictly throughout the code base):

* LLM            -> reasoning and orchestration assistance
* Python analytics -> deterministic computation (VWAP, IS, slippage, impact ...)
* MCP            -> capability interface
* Harness        -> control plane
* Policy engine  -> authority
* Evidence       -> auditability
"""

__version__ = "0.3.1"
