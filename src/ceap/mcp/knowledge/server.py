"""Knowledge MCP server: RAG over runbooks, policies and configuration documents."""

from __future__ import annotations

from typing import Any

from ceap.mcp.server import MCPServerDefinition
from ceap.rag.retrieval import KnowledgeBase


def build_server(knowledge: KnowledgeBase) -> MCPServerDefinition:
    server = MCPServerDefinition(
        "knowledge",
        "Enterprise knowledge: execution-algorithm documentation, VWAP runbook, TCA methodology, venue configuration, trading limits, incident runbooks and execution policy.",
    )

    @server.tool("Semantic search over enterprise documents; returns the best matching sections with scores.")
    async def search_documents(query: str, k: int = 4) -> dict[str, Any]:
        hits = knowledge.search(query, k=k)
        return {"query": query, "count": len(hits), "items": [h.to_dict() for h in hits]}

    @server.tool("Return a whole document by id.")
    async def get_document(document_id: str) -> dict[str, Any]:
        doc = knowledge.get(document_id)
        if doc is None:
            return {"document_id": document_id, "found": False}
        return {
            "document_id": doc.id,
            "found": True,
            "title": doc.title,
            "text": doc.text,
            "metadata": doc.metadata,
        }

    @server.tool("List the documents available in the knowledge base.")
    async def list_documents() -> dict[str, Any]:
        items = knowledge.titles()
        return {"count": len(items), "items": items}

    return server
