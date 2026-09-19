"""Demo: drive the real MCP servers as subprocesses over stdio.

    python scripts/run_stdio_mcp_demo.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from ceap.mcp.client import StdioMCPClient, StdioServerSpec
from ceap.mcp.registry import build_tool_registry


async def main() -> None:
    client = StdioMCPClient(
        [
            StdioServerSpec("market_data", sys.executable, ["-m", "ceap.mcp.market_data"]),
            StdioServerSpec("execution", sys.executable, ["-m", "ceap.mcp.execution"]),
            StdioServerSpec("risk", sys.executable, ["-m", "ceap.mcp.risk"]),
            StdioServerSpec("engineering", sys.executable, ["-m", "ceap.mcp.engineering"]),
            StdioServerSpec("knowledge", sys.executable, ["-m", "ceap.mcp.knowledge"]),
        ]
    )
    try:
        registry = await build_tool_registry(client)
        print(f"discovered {len(registry)} tools over stdio:")
        for tool_id in registry.ids():
            print("  ", tool_id)
        metrics = await client.invoke(
            "execution",
            "get_execution_metrics",
            {"symbol": "AAPL", "start": "2026-09-18T14:00:00", "end": "2026-09-18T15:00:00", "dataset": "T06"},
        )
        print(json.dumps({k: metrics[k] for k in ("vwap", "arrival_price", "implementation_shortfall_bps", "fill_rate")}, indent=2))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
