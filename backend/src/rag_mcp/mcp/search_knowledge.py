"""MCP tool definition for search_knowledge.

Registers the primary semantic search tool with the MCP server. Accepts
a natural language query and explicit project scopes, delegates to
RetrievalService, and returns structured content conforming to
mcp-search-output.schema.json.

The tool returns both structuredContent (dict) and mirrored TextContent
(JSON string) for maximum client compatibility per MCP spec §4.3.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


def register_search_knowledge_tool(
    mcp_server: FastMCP,
    session_factory: Any,
    qdrant_store: QdrantStore,
    embedding_provider: EmbeddingProvider,
) -> None:
    """Register the search_knowledge tool on the MCP server.

    Args:
        mcp_server: The FastMCP server instance.
        session_factory: Callable that returns an AsyncSession.
        qdrant_store: Qdrant vector store client.
        embedding_provider: Embedding provider for query vectorization.
    """

    @mcp_server.tool(
        name="search_knowledge",
        description=(
            "Search the RAG knowledge base for evidence relevant to a query. "
            "Requires explicit project scope(s) — full-library search is not allowed. "
            "Returns structured evidence items with relevance scores, source positions, "
            "and completion status indicating coverage quality."
        ),
    )
    async def search_knowledge(
        query: str,
        project_scope: list[str],
        top_k: int = 5,
        task_context: dict | None = None,
    ) -> dict[str, Any]:
        """Search the knowledge base for relevant evidence.

        Args:
            query: Natural language query or factual question (1-2000 chars).
            project_scope: List of project references (stable ID, alias, or repo path).
                At least one is required; full-library search is rejected.
            top_k: Maximum evidence items to return (1-20, default 5).
            task_context: Optional context about the current work phase, file, or symbol.

        Returns:
            Structured response with completion_status, evidence list, optional gaps,
            optional error, and request_id for tracing.
        """
        # Validate inputs
        if not query or not query.strip():
            return _error_response("Query must not be empty.", "INVALID_INPUT")

        if not project_scope or len(project_scope) == 0:
            return _error_response(
                "At least one project_scope entry is required. Full-library search is not allowed.",
                "MISSING_PROJECT_SCOPE",
            )

        # Clamp top_k
        settings = get_settings()
        top_k = max(1, min(top_k, settings.retrieval.top_k_max))

        try:
            # Create service with fresh session
            async with session_factory() as session:
                service = RetrievalService(
                    session=session,
                    qdrant_store=qdrant_store,
                    embedding_provider=embedding_provider,
                )
                result = await service.search(
                    query=query.strip(),
                    project_scopes=project_scope,
                    top_k=top_k,
                    task_context=task_context,
                )
                await session.commit()
                return result

        except Exception as exc:
            logger.error("search_knowledge tool failed: %s", exc, exc_info=True)
            return _error_response(
                f"Internal error during search: {type(exc).__name__}",
                "SYSTEM_ERROR",
            )


def _error_response(message: str, code: str) -> dict[str, Any]:
    """Build a minimal error response for tool-level failures."""
    import uuid

    return {
        "completion_status": "failed",
        "evidence": [],
        "error": {
            "code": code,
            "message": message,
        },
        "request_id": str(uuid.uuid4()),
    }
