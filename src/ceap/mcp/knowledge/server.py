"""Knowledge MCP server: lexical BM25 retrieval over versioned runbooks, policies and methodology."""

from __future__ import annotations

from typing import Any

from ceap.mcp.server import MCPServerDefinition
from ceap.rag.query import DEFAULT_CLASSIFICATIONS, parse_classifications
from ceap.rag.retrieval import KnowledgeBase


def build_server(knowledge: KnowledgeBase) -> MCPServerDefinition:
    server = MCPServerDefinition(
        "knowledge",
        "Enterprise knowledge: execution-algorithm documentation, VWAP runbook, TCA methodology, attribution threshold tables, quant methodology, venue configuration, trading limits, incident runbooks and execution policy.",
    )

    @server.tool(
        "Keyword (BM25) search over enterprise documents; returns matching sections with document, version, "
        "effective date and score. An empty result means no document supports the query. "
        "classifications is comma-separated and defaults to public,internal; the policy engine decides who may ask for more."
    )
    async def search_documents(query: str, k: int = 4, classifications: str = "") -> dict[str, Any]:
        readable = parse_classifications(classifications) or DEFAULT_CLASSIFICATIONS
        hits = knowledge.search(query, k=k, classifications=readable)
        return {
            "query": query,
            "count": len(hits),
            "corpus_hash": knowledge.corpus_hash,
            "items": [h.to_dict() for h in hits],
        }

    @server.tool("Return a whole document by id (same classification rule as search_documents).")
    async def get_document(document_id: str, classifications: str = "") -> dict[str, Any]:
        readable = parse_classifications(classifications) or DEFAULT_CLASSIFICATIONS
        doc = knowledge.get(document_id)
        if doc is None or doc.classification not in readable:
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
        return {"count": len(items), "corpus_hash": knowledge.corpus_hash, "items": items}

    return server
