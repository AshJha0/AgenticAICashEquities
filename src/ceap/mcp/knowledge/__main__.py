"""Run the Knowledge MCP server over stdio: ``python -m ceap.mcp.knowledge``."""

from ceap.mcp.knowledge.server import build_server
from ceap.rag.retrieval import build_knowledge_base

if __name__ == "__main__":
    build_server(build_knowledge_base()).run_stdio()
