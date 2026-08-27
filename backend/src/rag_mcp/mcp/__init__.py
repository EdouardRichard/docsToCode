"""MCP server package for RAG knowledge retrieval tools.

Provides ``create_mcp_server()`` which assembles a FastMCP server with
the ``search_knowledge`` and ``get_evidence`` tools registered, configured
for Streamable HTTP transport on 127.0.0.1:8080 (configurable via settings).

Blueprint §5 (MCP integration), §22 (tool contracts).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)


def _default_session_factory() -> Callable[[], AsyncIterator[AsyncSession]]:
    """Create a default async session factory from application settings.

    Returns an async context manager that yields an AsyncSession per call.
    This is used when no external session factory is provided to
    ``create_mcp_server()``.
    """
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def session_context() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    return session_context


def create_mcp_server(
    session_factory: Callable[[], Any] | None = None,
    qdrant_store: QdrantStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastMCP:
    """Create and configure the MCP server with all RAG tools.

    Args:
        session_factory: Async context manager yielding AsyncSession instances.
            If None, creates one from DATABASE_URL in settings.
        qdrant_store: Qdrant vector store client. If None, creates one from
            QDRANT_URL in settings.
        embedding_provider: Embedding provider for query vectorization.
            Must be provided externally (no default implementation bundled).

    Returns:
        Configured FastMCP server instance ready to serve requests.

    Raises:
        ValueError: If embedding_provider is not supplied.
    """
    if embedding_provider is None:
        raise ValueError(
            "embedding_provider must be supplied to create_mcp_server(). "
            "No default embedding implementation is bundled."
        )

    settings = get_settings()

    # Resolve dependencies
    if session_factory is None:
        session_factory = _default_session_factory()

    if qdrant_store is None:
        qdrant_store = QdrantStore()

    # Create MCP server instance
    mcp_server = FastMCP(
        name="rag-mcp-server",
        host="127.0.0.1",
        port=settings.mcp_port,
        streamable_http_path="/mcp",
    )

    # Register tools
    from rag_mcp.mcp.search_knowledge import register_search_knowledge_tool
    from rag_mcp.mcp.get_evidence import register_get_evidence_tool

    register_search_knowledge_tool(
        mcp_server=mcp_server,
        session_factory=session_factory,
        qdrant_store=qdrant_store,
        embedding_provider=embedding_provider,
    )

    register_get_evidence_tool(
        mcp_server=mcp_server,
        session_factory=session_factory,
    )

    logger.info(
        "MCP server created with tools: search_knowledge, get_evidence "
        "(host=127.0.0.1, port=%d)",
        settings.mcp_port,
    )

    return mcp_server


__all__ = ["create_mcp_server"]
