"""IngestionService: orchestrates document ingestion, parsing, embedding, and indexing.

Handles the full lifecycle of a KnowledgeSource from raw file to published
KnowledgeVersion with Qdrant vectors and PostgreSQL Chunk metadata.

Implements FR-009: old version stays published until new version publish succeeds.
"""

from __future__ import annotations

import logging
import math
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client.models import PointStruct
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.models import (
    Chunk,
    KnowledgeSource,
    KnowledgeVersion,
    ProcessingRun,
)
from rag_mcp.parsers.credential_redactor import redact_credentials
from rag_mcp.parsers.java_parser import JavaParser
from rag_mcp.parsers.markdown_parser import MarkdownParser
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

# Approximate chars-per-token ratio for fallback token estimation
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate based on character length."""
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def _derive_index_version(embedding_model: str) -> str:
    """Derive an index_version identifier from the embedding model name.

    Convention: ``<model-short-name>_v1``.  The short name is the last
    path component of the HuggingFace model ID with slashes replaced by
    hyphens (e.g. ``BAAI/bge-m3`` → ``bge-m3_v1``).
    """
    short_name = embedding_model.rsplit("/", 1)[-1]
    return f"{short_name}_v1"


def backfill_parent_chunk_ids(chunk_dicts: list[dict[str, Any]]) -> None:
    """Backfill ``parent_chunk_id`` for each chunk in-place (FR-007 / US-3).

    Two-pass traversal over the parser output:

    1. Build a ``position_path -> chunk_id`` map from each chunk's own
       position path (``section_path`` for Markdown, ``symbol_path`` for Java).
    2. Resolve each chunk's explicit parent reference (``parent_section_path``
       for Markdown, ``parent_symbol_path`` for Java) against that map, and set
       ``parent_chunk_id`` to the matched parent's id.

    A chunk keeps no ``parent_chunk_id`` when its parent reference is empty,
    unresolvable, or equal to its own path. This makes the ``get_evidence``
    parent-context path triggerable for hierarchical chunks.
    """
    path_to_id: dict[str, int] = {}
    for chunk in chunk_dicts:
        position_path = (
            chunk.get("section_path") or chunk.get("symbol_path") or ""
        )
        if position_path:
            path_to_id[position_path] = chunk["chunk_id"]

    for chunk in chunk_dicts:
        parent_path = (
            chunk.get("parent_section_path")
            or chunk.get("parent_symbol_path")
            or ""
        )
        if not parent_path:
            continue
        parent_id = path_to_id.get(parent_path)
        if parent_id is not None and parent_id != chunk["chunk_id"]:
            chunk["parent_chunk_id"] = parent_id


class IngestionService:
    """Orchestrates document ingestion into the RAG knowledge base.

    Coordinates credential redaction, format-specific parsing, batch
    embedding, Qdrant upsert, and PostgreSQL metadata creation within
    a single transactional workflow.
    """

    def __init__(
        self,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
        qdrant_store: QdrantStore,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider
        self._qdrant_store = qdrant_store
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(self, source_id: int) -> None:
        """Run initial ingestion for a KnowledgeSource.

        Creates a ProcessingRun with ``run_type='initial'`` and executes
        the full ingestion pipeline.

        Args:
            source_id: Snowflake ID of the KnowledgeSource to ingest.

        Raises:
            ValueError: If the source does not exist.
        """
        await self._run_pipeline(source_id, run_type="initial")

    async def reprocess(self, source_id: int) -> None:
        """Re-process a previously failed or completed KnowledgeSource.

        Creates a ProcessingRun with ``run_type='retry'`` and executes
        the full ingestion pipeline.

        Args:
            source_id: Snowflake ID of the KnowledgeSource to reprocess.

        Raises:
            ValueError: If the source does not exist.
        """
        await self._run_pipeline(source_id, run_type="retry")

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, source_id: int, run_type: str) -> None:
        """Execute the full ingestion pipeline with error handling.

        On success the KnowledgeSource transitions to ``published`` and
        a new KnowledgeVersion is created.  On failure both the
        ProcessingRun and KnowledgeSource are marked ``failed``.
        """
        # 1. Load KnowledgeSource
        source = await self._load_source(source_id)

        # 2. Create ProcessingRun
        run = await self._create_processing_run(source_id, run_type)

        try:
            # Mark source as processing
            source.status = "processing"
            source.processing_error = None
            await self._session.flush()

            # Update run to running
            now = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = now
            await self._session.flush()

            stages: list[dict[str, Any]] = []

            # 3. Read raw file
            raw_content = await self._read_raw_file(source)

            # 4. Redact credentials
            stage_start = datetime.now(timezone.utc)
            redacted_text = redact_credentials(raw_content)
            stages.append({
                "stage": "credential_scan",
                "status": "completed",
                "started_at": stage_start.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "details": {},
            })

            # 5. Parse based on format
            stage_start = datetime.now(timezone.utc)
            chunk_dicts = self._parse_content(redacted_text, source.format, source.filename)
            stages.append({
                "stage": "parsing",
                "status": "completed",
                "started_at": stage_start.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "details": {"chunks_parsed": len(chunk_dicts)},
            })

            if not chunk_dicts:
                logger.warning("No chunks produced from source %s (%s)", source_id, source.filename)
                raise ValueError(f"No chunks produced from parsing {source.filename}")

            # 6. Generate chunk IDs and ensure token counts
            stage_start = datetime.now(timezone.utc)
            for chunk_dict in chunk_dicts:
                chunk_dict["chunk_id"] = generate_id()
                if "token_count" not in chunk_dict or chunk_dict["token_count"] <= 0:
                    chunk_dict["token_count"] = _estimate_tokens(chunk_dict["content_text"])

            # Backfill parent_chunk_id via two-pass traversal (FR-007 / US-3)
            backfill_parent_chunk_ids(chunk_dicts)
            stages.append({
                "stage": "chunking",
                "status": "completed",
                "started_at": stage_start.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "details": {"chunks_created": len(chunk_dicts)},
            })

            # 7. Batch embed all chunk texts
            stage_start = datetime.now(timezone.utc)
            texts = [c["content_text"] for c in chunk_dicts]
            embeddings = await self._embedding_provider.embed_texts(texts)
            embedding_model = self._settings.embedding_model
            stages.append({
                "stage": "embedding",
                "status": "completed",
                "started_at": stage_start.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "details": {"model": embedding_model, "vectors": len(embeddings)},
            })

            # 8. Determine index_version
            index_version = _derive_index_version(embedding_model)
            collection_name = f"chunks_dense_{index_version}"

            # 9. Ensure Qdrant collection exists
            dimension = self._embedding_provider.get_dimension()
            if not self._qdrant_store.collection_exists(collection_name):
                logger.info("Creating Qdrant collection: %s (dim=%d)", collection_name, dimension)
                self._qdrant_store.create_collection(collection_name, dimension)

            # 10–11. Create KnowledgeVersion, Chunks, and Qdrant points
            #   Write order per data-model §7.2:
            #     a) PostgreSQL Chunks first
            #     b) Qdrant points second
            #     c) Version status update last (atomic publish)

            scope_id = source.knowledge_scope_id

            # Determine next version number
            version_number = await self._get_next_version_number(scope_id)
            version_id = generate_id()

            # Create KnowledgeVersion in draft status
            version = KnowledgeVersion(
                version_id=version_id,
                knowledge_scope_id=scope_id,
                version_number=version_number,
                capabilities={"dense_ready": True},
                status="draft",
                created_at=datetime.now(timezone.utc),
            )
            self._session.add(version)
            await self._session.flush()

            # Create Chunk records in PostgreSQL
            chunk_records: list[Chunk] = []
            for i, chunk_dict in enumerate(chunk_dicts):
                # Determine position_path based on format
                if source.format == "markdown":
                    position_path = chunk_dict.get("section_path", "")
                else:
                    position_path = chunk_dict.get("symbol_path", "")

                chunk_record = Chunk(
                    chunk_id=chunk_dict["chunk_id"],
                    source_id=source_id,
                    version_id=version_id,
                    knowledge_scope_id=scope_id,
                    parent_chunk_id=chunk_dict.get("parent_chunk_id"),
                    content_text=chunk_dict["content_text"],
                    position_path=position_path,
                    chunk_type=chunk_dict["chunk_type"],
                    start_line=chunk_dict["start_line"],
                    end_line=chunk_dict["end_line"],
                    token_count=chunk_dict["token_count"],
                    embedding_model=embedding_model,
                    index_version=index_version,
                )
                chunk_records.append(chunk_record)

            self._session.add_all(chunk_records)
            await self._session.flush()

            logger.info(
                "Created %d Chunk records for source %s, version %d",
                len(chunk_records), source_id, version_number,
            )

            # Upsert points to Qdrant
            points: list[PointStruct] = []
            for i, chunk_dict in enumerate(chunk_dicts):
                point = PointStruct(
                    id=chunk_dict["chunk_id"],
                    vector=embeddings[i],
                    payload={
                        "knowledge_scope_id": str(scope_id),
                        "source_id": str(source_id),
                        "version_id": str(version_id),
                        "chunk_id": str(chunk_dict["chunk_id"]),
                        "chunk_type": chunk_dict["chunk_type"],
                        "position_path": chunk_dict.get("section_path", "")
                            if source.format == "markdown"
                            else chunk_dict.get("symbol_path", ""),
                        "start_line": chunk_dict["start_line"],
                        "end_line": chunk_dict["end_line"],
                        "index_version": index_version,
                        "embedding_model": embedding_model,
                    },
                )
                points.append(point)

            self._qdrant_store.upsert_points(collection_name, points)
            logger.info(
                "Upserted %d points to Qdrant collection %s",
                len(points), collection_name,
            )

            # 12. Publish the new version — only after PG + Qdrant succeed
            #   FR-009: supersede old version AFTER new version is published
            await self._publish_version(version, scope_id)

            # 13. Update KnowledgeSource status to 'published'
            source.status = "published"
            source.processing_error = None
            await self._session.flush()

            # 14. Mark ProcessingRun as completed
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.stages = stages
            await self._session.flush()

            # 15. COMMIT the transaction — all PG changes persist atomically
            await self._session.commit()

            logger.info(
                "Ingestion completed successfully for source %s (version %d)",
                source_id, version_number,
            )

        except Exception as exc:
            # 15. On any exception: fail the run and the source
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Ingestion failed for source %s: %s\n%s",
                source_id, error_msg, traceback.format_exc(),
            )

            # Best-effort: update run and source status even if session is dirty
            try:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                run.error_message = error_msg
                # stages may not exist if failure occurred before initialization
                try:
                    run.stages = stages  # type: ignore[possibly-undefined]
                except NameError:
                    run.stages = []

                source.status = "failed"
                source.processing_error = error_msg

                await self._session.flush()
                await self._session.commit()
            except Exception as inner_exc:
                logger.error(
                    "Failed to update error state for source %s: %s",
                    source_id, inner_exc,
                )
                await self._session.rollback()

            raise

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _load_source(self, source_id: int) -> KnowledgeSource:
        """Load a KnowledgeSource by ID, raising ValueError if not found."""
        result = await self._session.execute(
            select(KnowledgeSource).where(KnowledgeSource.source_id == source_id)
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise ValueError(f"KnowledgeSource {source_id} not found")
        return source

    async def _create_processing_run(
        self, source_id: int, run_type: str
    ) -> ProcessingRun:
        """Create a new ProcessingRun record."""
        run = ProcessingRun(
            run_id=generate_id(),
            source_id=source_id,
            run_type=run_type,
            status="pending",
            stages=[],
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def _read_raw_file(self, source: KnowledgeSource) -> str:
        """Read the raw file content from the data root.

        File path convention: ``{data_root}/{scope_id}/{source_id}/{filename}``
        """
        data_root = Path(self._settings.data_root)
        file_path = (
            data_root
            / str(source.knowledge_scope_id)
            / str(source.source_id)
            / source.filename
        )

        if not file_path.exists():
            raise FileNotFoundError(
                f"Raw file not found at {file_path} for source {source.source_id}"
            )

        content = file_path.read_text(encoding="utf-8")
        if not content.strip():
            raise ValueError(
                f"Empty file content for source {source.source_id} ({source.filename})"
            )

        logger.info(
            "Read %d bytes from %s for source %s",
            len(content), file_path, source.source_id,
        )
        return content

    def _parse_content(
        self, text: str, fmt: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse redacted text using the appropriate format parser.

        Args:
            text: Credential-redacted content.
            fmt: Format string ('markdown' or 'java').
            filename: Original filename for diagnostics.

        Returns:
            List of chunk dicts from the parser.

        Raises:
            ValueError: If the format is unsupported.
        """
        if fmt == "markdown":
            parser = MarkdownParser()
            return parser.parse(text)
        elif fmt == "java":
            parser = JavaParser()
            return parser.parse(text, filename=filename)
        else:
            raise ValueError(
                f"Unsupported format '{fmt}' for file {filename}. "
                f"Supported formats: markdown, java"
            )

    async def _get_next_version_number(self, scope_id: int) -> int:
        """Determine the next monotonically increasing version number for a scope.

        Locks the scope row with ``SELECT ... FOR UPDATE`` so that concurrent
        ingestion tasks targeting the same scope serialize their version-number
        allocation. Without the lock, two tasks can both read ``max=0`` and both
        insert ``version_number=1``, violating the ``(knowledge_scope_id,
        version_number)`` unique constraint (observed race).
        """
        from rag_mcp.models.knowledge_scope import KnowledgeScope

        await self._session.execute(
            select(KnowledgeScope.scope_id)
            .where(KnowledgeScope.scope_id == scope_id)
            .with_for_update()
        )
        result = await self._session.execute(
            select(func.max(KnowledgeVersion.version_number)).where(
                KnowledgeVersion.knowledge_scope_id == scope_id
            )
        )
        max_version = result.scalar_one_or_none()
        return (max_version or 0) + 1

    async def _publish_version(
        self, version: KnowledgeVersion, scope_id: int
    ) -> None:
        """Publish a KnowledgeVersion and supersede the previous published version.

        FR-009 compliance: the old version is superseded ONLY after the new
        version has been successfully written to both PostgreSQL and Qdrant.
        This method performs the status transitions atomically within the
        current session.
        """
        now = datetime.now(timezone.utc)

        # Supersede any currently-published version in this scope
        existing_result = await self._session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.knowledge_scope_id == scope_id,
                KnowledgeVersion.status == "published",
            )
        )
        old_published = existing_result.scalars().all()
        for old_version in old_published:
            old_version.status = "superseded"
            logger.info(
                "Superseded version %d (id=%s) in scope %s",
                old_version.version_number, old_version.version_id, scope_id,
            )
            # FR-009: the old version stays searchable until the new version is
            # written; now that the switch is happening, purge the superseded
            # version's derived data (Qdrant points + PG chunks) so it does not
            # linger as orphaned/stale data.
            await self._cleanup_version_derived_data(old_version.version_id)

        # Publish the new version
        version.status = "published"
        version.published_at = now
        await self._session.flush()

        logger.info(
            "Published version %d (id=%s) in scope %s",
            version.version_number, version.version_id, scope_id,
        )

    async def _cleanup_version_derived_data(self, version_id: int) -> None:
        """Purge a superseded version's Qdrant points and PG chunks.

        Called from ``_publish_version`` after a version is marked superseded.
        Idempotent and tolerant of Qdrant failures (which must never block the
        PG chunk deletion).
        """
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import select

        from rag_mcp.models.chunk import Chunk

        result = await self._session.execute(
            select(Chunk.index_version).where(Chunk.version_id == version_id).distinct()
        )
        index_versions = list(result.scalars().all())

        for index_version in index_versions:
            collection = f"chunks_dense_{index_version}"
            try:
                self._qdrant_store.delete_points_by_version(collection, version_id)
            except Exception:  # noqa: BLE001 - Qdrant outage must not block PG cleanup
                logger.exception(
                    "Failed to purge Qdrant points for version %s (collection %s)",
                    version_id,
                    collection,
                )

        await self._session.execute(
            sa_delete(Chunk).where(Chunk.version_id == version_id)
        )
