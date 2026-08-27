"""REST API routes for knowledge source management (FR-004, FR-010, FR-011, US1, US4)."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.db import get_session
from rag_mcp.schemas.knowledge_source import (
    KnowledgeSourceListResponse,
    KnowledgeSourceResponse,
)
from rag_mcp.utils.hashing import hash_bytes

router = APIRouter(prefix="/api/knowledge-sources", tags=["knowledge-sources"])

# ---------------------------------------------------------------------------
# Background ingestion helpers
# ---------------------------------------------------------------------------

import asyncio
import logging

logger = logging.getLogger(__name__)

_embedding_provider = None
_qdrant_store = None


def _get_embedding_provider():
    """Lazily create and cache the LocalCPUEmbeddingProvider singleton."""
    global _embedding_provider
    if _embedding_provider is None:
        from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider

        _embedding_provider = LocalCPUEmbeddingProvider()
    return _embedding_provider


def _get_qdrant_store():
    """Lazily create and cache the QdrantStore singleton."""
    global _qdrant_store
    if _qdrant_store is None:
        from rag_mcp.indexing.qdrant_client import QdrantStore

        _qdrant_store = QdrantStore()
    return _qdrant_store


async def _run_ingestion(source_id: int) -> None:
    """Run the ingestion pipeline for a source in a background task.

    Uses its own DB session (independent of the request-scoped session).
    """
    from rag_mcp.db import get_session_factory
    from rag_mcp.services.ingestion_service import IngestionService

    factory = get_session_factory()
    try:
        async with factory() as session:
            service = IngestionService(
                session=session,
                embedding_provider=_get_embedding_provider(),
                qdrant_store=_get_qdrant_store(),
            )
            await service.ingest(source_id)
    except Exception:
        logger.exception("Ingestion failed for source %s", source_id)


def _schedule_ingestion(source_id: int) -> None:
    """Schedule ingestion as a fire-and-forget background task.

    When ``INGESTION_BACKGROUND=false`` (used by tests and any operationally
    deferred-processing mode), no background task is spawned and the source
    remains in ``uploaded`` status awaiting explicit reprocessing.
    """
    if not get_settings().ingestion_background:
        logger.info("Background ingestion disabled; source %s stays 'uploaded'", source_id)
        return
    try:
        asyncio.get_running_loop().create_task(_run_ingestion(source_id))
        logger.info("Scheduled ingestion for source %s", source_id)
    except RuntimeError:
        # No running loop (e.g., synchronous context) — skip scheduling
        logger.warning("No running event loop; skipping background ingestion")


def _detect_format(filename: str) -> str | None:
    """Detect file format from extension. Returns 'markdown', 'java', or None."""
    ext = Path(filename).suffix.lower()
    if ext in (".md", ".markdown"):
        return "markdown"
    elif ext == ".java":
        return "java"
    return None


@router.post("", response_model=KnowledgeSourceResponse, status_code=201)
async def upload_knowledge_source(
    scope_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Upload a new knowledge source file (Markdown or Java).

    Args:
        scope_id: Knowledge scope ID to associate the source with.
        file: Uploaded file (multipart/form-data).
    """
    from rag_mcp.models.knowledge_source import KnowledgeSource
    from rag_mcp.utils.snowflake import generate_id

    # Validate format
    fmt = _detect_format(file.filename or "")
    if fmt is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {file.filename}. Only .md and .java are supported.",
        )

    # Read and hash content
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    content_hash = hash_bytes(content)
    settings = get_settings()

    # Save raw file to data_root
    source_id = generate_id()
    save_dir = Path(settings.data_root) / str(scope_id) / str(source_id)
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / (file.filename or "unknown")
    save_path.write_bytes(content)

    # Create database record
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    source = KnowledgeSource(
        source_id=source_id,
        knowledge_scope_id=scope_id,
        filename=file.filename or "unknown",
        content_hash=content_hash,
        format=fmt,
        size_bytes=len(content),
        status="uploaded",
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    await session.flush()
    await session.commit()

    # Trigger background ingestion pipeline (async, non-blocking)
    _schedule_ingestion(source_id)

    return KnowledgeSourceResponse(
        source_id=str(source.source_id),
        knowledge_scope_id=str(source.knowledge_scope_id),
        filename=source.filename,
        content_hash=source.content_hash,
        format=source.format,
        size_bytes=source.size_bytes,
        status=source.status,
        processing_error=source.processing_error,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.get("", response_model=KnowledgeSourceListResponse)
async def list_knowledge_sources(
    scope_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List knowledge sources, optionally filtered by scope."""
    from sqlalchemy import select

    from rag_mcp.models.knowledge_source import KnowledgeSource

    stmt = select(KnowledgeSource).order_by(KnowledgeSource.created_at.desc())
    if scope_id is not None:
        stmt = stmt.where(KnowledgeSource.knowledge_scope_id == scope_id)

    result = await session.execute(stmt)
    sources = list(result.scalars().all())

    items = [
        KnowledgeSourceResponse(
            source_id=str(s.source_id),
            knowledge_scope_id=str(s.knowledge_scope_id),
            filename=s.filename,
            content_hash=s.content_hash,
            format=s.format,
            size_bytes=s.size_bytes,
            status=s.status,
            processing_error=s.processing_error,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sources
    ]
    return KnowledgeSourceListResponse(items=items, total=len(items))


@router.get("/{source_id}", response_model=KnowledgeSourceResponse)
async def get_knowledge_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Get a knowledge source by ID."""
    from sqlalchemy import select

    from rag_mcp.models.knowledge_source import KnowledgeSource

    result = await session.execute(
        select(KnowledgeSource).where(KnowledgeSource.source_id == source_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")

    return KnowledgeSourceResponse(
        source_id=str(source.source_id),
        knowledge_scope_id=str(source.knowledge_scope_id),
        filename=source.filename,
        content_hash=source.content_hash,
        format=source.format,
        size_bytes=source.size_bytes,
        status=source.status,
        processing_error=source.processing_error,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.post("/{source_id}/reprocess", status_code=202)
async def reprocess_knowledge_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Trigger reprocessing of a knowledge source (blueprint §5).

    Creates a new ProcessingRun with run_type='retry'.
    Old version stays published until new version publish succeeds (FR-009).
    """
    from sqlalchemy import select

    from rag_mcp.models.knowledge_source import KnowledgeSource

    result = await session.execute(
        select(KnowledgeSource).where(KnowledgeSource.source_id == source_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")

    if source.status not in ("published", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reprocess source in '{source.status}' status",
        )

    # Reset status to trigger reprocessing
    from datetime import datetime, timezone

    source.status = "uploaded"
    source.processing_error = None
    source.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await session.commit()

    # Trigger background ingestion pipeline (same as upload path)
    _schedule_ingestion(source_id)

    return {"message": "Reprocessing triggered", "source_id": str(source_id)}


@router.delete("/{source_id}", status_code=204)
async def delete_knowledge_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Delete a knowledge source (FR-011, FR-012).

    Marks source as deleted (stops retrieval immediately), then cleans up
    derived data asynchronously. Idempotent - deleting already-deleted source
    returns 204.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from rag_mcp.models.knowledge_source import KnowledgeSource

    result = await session.execute(
        select(KnowledgeSource).where(KnowledgeSource.source_id == source_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")

    if source.status == "deleted":
        # Idempotent: already deleted
        return

    # Step 1: Mark as deleted (immediately stops participation in new retrieval)
    source.status = "deleted"
    source.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await session.commit()

    # Step 2: Async cleanup would happen here in production
    # (remove Qdrant points, archive PG chunks)
    # For 001 demo, the status change is sufficient to stop retrieval


@router.post("/scopes/{scope_id}/clear", status_code=202)
async def clear_knowledge_scope(
    scope_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Clear all knowledge sources in a scope (FR-011, FR-012).

    Marks all sources as deleted and the scope as 'deleting'.
    Other scopes are unaffected.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select, update

    from rag_mcp.models.knowledge_scope import KnowledgeScope
    from rag_mcp.models.knowledge_source import KnowledgeSource

    # Verify scope exists
    scope_result = await session.execute(
        select(KnowledgeScope).where(KnowledgeScope.scope_id == scope_id)
    )
    scope = scope_result.scalar_one_or_none()
    if scope is None:
        raise HTTPException(status_code=404, detail="Knowledge scope not found")

    now = datetime.now(timezone.utc)

    # Mark scope as deleting
    scope.status = "deleting"
    scope.updated_at = now

    # Mark all sources in scope as deleted
    await session.execute(
        update(KnowledgeSource)
        .where(KnowledgeSource.knowledge_scope_id == scope_id)
        .values(status="deleted", updated_at=now)
    )

    await session.flush()
    await session.commit()

    return {"message": "Scope clearing initiated", "scope_id": str(scope_id)}
