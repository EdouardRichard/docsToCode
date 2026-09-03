"""FastAPI application entry point for RAG MCP Server.

Provides:
- REST management API on management_port (default 8000)
- Health endpoint
- Static file mount for frontend build (production)
- CORS for localhost development
- Lifespan-managed DB engine, shared services, and a periodic TTL cleanup loop
  (blueprint §20) that purges expired RetrievalRun records.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from rag_mcp.api.middleware import RequestContextMiddleware
from rag_mcp.config import get_settings

logger = logging.getLogger(__name__)


async def _ttl_cleanup_loop(interval_s: int) -> None:
    """Periodically purge expired retrieval run records (blueprint §20).

    Covers the 001 retrieval_runs audit table and the 005 Agent
    orchestration runtime tables (T066).
    """
    from rag_mcp.db import get_session_factory
    from rag_mcp.services.maintenance_service import (
        purge_expired_agentic_runs,
        purge_expired_retrieval_runs,
    )

    while True:
        await asyncio.sleep(interval_s)
        try:
            factory = get_session_factory()
            async with factory() as session:
                await purge_expired_retrieval_runs(session)
                await purge_expired_agentic_runs(session)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive on transient errors
            logger.exception("TTL cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: TTL cleanup loop + DB engine disposal."""
    settings = get_settings()
    cleanup_task = asyncio.create_task(
        _ttl_cleanup_loop(settings.retrieval_ttl_cleanup_interval_s)
    )
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        from rag_mcp.db import dispose_engine

        await dispose_engine()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="RAG MCP Management API",
        description="AI Engineering RAG MCP Server - Management REST API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    # Register API routers
    from rag_mcp.api.projects import router as projects_router
    from rag_mcp.api.knowledge_sources import router as ks_router
    from rag_mcp.api.sse import router as sse_router

    app.include_router(projects_router)
    app.include_router(ks_router)
    app.include_router(sse_router)

    # Mount frontend static files if build exists
    frontend_dist = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


app = create_app()


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="RAG MCP management API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Management API port (default from MANAGEMENT_PORT, 8000)",
    )
    parser.add_argument(
        "--mode",
        default="writer",
        choices=["writer", "reader"],
        help="Instance mode (reader reserved for 006; writer only for 001)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=None,
        help="MCP port (informational; the MCP server runs via backend/_run_mcp.py)",
    )
    args = parser.parse_args()

    port = args.port or get_settings().management_port
    logger.info("Starting management API (%s mode) on %s:%d", args.mode, args.host, port)
    uvicorn.run("rag_mcp.server:app", host=args.host, port=port)
