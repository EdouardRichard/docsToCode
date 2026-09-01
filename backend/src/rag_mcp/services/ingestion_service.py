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
from rag_mcp.parsers.text_extractor import BINARY_FORMATS, extract_text, TextExtractionError
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
            chunk.get("section_path")
            or chunk.get("symbol_path")
            or chunk.get("structure_path")
            or ""
        )
        if position_path:
            path_to_id[position_path] = chunk["chunk_id"]

    for chunk in chunk_dicts:
        parent_path = (
            chunk.get("parent_section_path")
            or chunk.get("parent_symbol_path")
            or chunk.get("parent_structure_path")
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
        soft_relation_llm: Any | None = None,
        soft_relation_model_and_version: str = "offline-llm-v1",
    ) -> None:
        """Initialise the ingestion service.

        Args:
            session: Async SQLAlchemy session.
            embedding_provider: Dense embedding provider.
            qdrant_store: Qdrant vector store.
            soft_relation_llm: Optional offline LLM callable for soft-relation
                inference (004, FR-003). Signature: llm(chunks) -> sequence of
                (source_chunk_id, target_chunk_id, confidence,
                supporting_evidence_ids). When None, soft inference is skipped.
            soft_relation_model_and_version: Provenance metadata recorded on
                inferred soft relations (metadata field 3).
        """
        self._session = session
        self._embedding_provider = embedding_provider
        self._qdrant_store = qdrant_store
        self._soft_relation_llm = soft_relation_llm
        self._soft_relation_model_and_version = soft_relation_model_and_version
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(self, source_id: int, graph_ready: bool = False) -> None:
        """Run initial ingestion for a KnowledgeSource.

        Creates a ProcessingRun with run_type='initial' and executes
        the full ingestion pipeline.

        Args:
            source_id: Snowflake ID of the KnowledgeSource to ingest.
            graph_ready: User declaration (004, FR-013/FR-027) that the new
                version should declare the graph_ready capability. Publishing
                still requires the graph relations to be ready; otherwise the
                version is NOT published and the run fails (FR-013).

        Raises:
            ValueError: If the source does not exist.
        """
        await self._run_pipeline(source_id, run_type="initial",
                                 request_graph_ready=graph_ready)

    async def reprocess(self, source_id: int, graph_ready: bool = False) -> None:
        """Re-process a previously failed or completed KnowledgeSource.

        Creates a ProcessingRun with run_type='retry' and executes
        the full ingestion pipeline. This is the user-triggered rebuild path
        by which an existing hybrid knowledge source gains graph_ready
        (FR-027); no automatic batch migration ever sets it.

        Args:
            source_id: Snowflake ID of the KnowledgeSource to reprocess.
            graph_ready: Optional user declaration for the new version.

        Raises:
            ValueError: If the source does not exist.
        """
        await self._run_pipeline(source_id, run_type="retry",
                                 request_graph_ready=graph_ready)

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self, source_id: int, run_type: str, request_graph_ready: bool = False
    ) -> None:
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

            # 3. Read raw file as bytes
            raw_bytes = await self._read_raw_bytes(source)

            # 3b. Text extraction for binary formats (FR-011, before credential_scan)
            if source.format in BINARY_FORMATS:
                stage_start = datetime.now(timezone.utc)
                try:
                    text_content = extract_text(raw_bytes, source.format)
                except TextExtractionError as exc:
                    raise ValueError(str(exc)) from exc
                stages.append({
                    "stage": "text_extraction",
                    "status": "completed",
                    "started_at": stage_start.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "details": {"format": source.format},
                })
            else:
                text_content = raw_bytes.decode("utf-8", errors="replace")

            # 4. Redact credentials
            stage_start = datetime.now(timezone.utc)
            redacted_text = redact_credentials(text_content)
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

            # 7b. Build sparse vectors (BM25 + jieba CJK, FR-001/FR-025)
            stage_start = datetime.now(timezone.utc)
            from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder

            sparse_encoder = BM25SparseEncoder()
            sparse_encoder.fit(texts)  # Fit on chunk texts (vocab frozen after)
            sparse_vectors = [sparse_encoder.encode(t) for t in texts]
            stages.append({
                "stage": "sparse_index",
                "status": "completed",
                "started_at": stage_start.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "details": {
                    "encoder": "bm25_jieba",
                    "vectors": len(sparse_vectors),
                    "tokenizer": "jieba_precise",
                },
            })

            # 8. Determine index_version and collection name
            index_version = _derive_index_version(embedding_model)
            collection_name = f"chunks_hybrid_{index_version}"

            # 9. Ensure Qdrant hybrid collection exists (Dense + Sparse named vectors)
            dimension = self._embedding_provider.get_dimension()
            if not self._qdrant_store.collection_exists(collection_name):
                logger.info("Creating Qdrant hybrid collection: %s (dim=%d)", collection_name, dimension)
                self._qdrant_store.create_hybrid_collection(collection_name, dimension)

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
                capabilities={"dense_ready": True, "lexical_ready": True},
                status="draft",
                created_at=datetime.now(timezone.utc),
            )
            self._session.add(version)
            await self._session.flush()

            # Create Chunk records in PostgreSQL
            chunk_records: list[Chunk] = []
            for i, chunk_dict in enumerate(chunk_dicts):
                # Determine position_path based on format — 003 extends to all path types
                position_path = (
                    chunk_dict.get("section_path")
                    or chunk_dict.get("symbol_path")
                    or chunk_dict.get("structure_path")
                    or ""
                )

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

            # Upsert hybrid points to Qdrant (Dense + Sparse on same Point, FR-001/FR-002)
            for i, chunk_dict in enumerate(chunk_dicts):
                position_path = (
                    chunk_dict.get("section_path")
                    or chunk_dict.get("symbol_path")
                    or chunk_dict.get("structure_path")
                    or ""
                )
                payload = {
                    "knowledge_scope_id": str(scope_id),
                    "source_id": str(source_id),
                    "version_id": str(version_id),
                    "chunk_id": str(chunk_dict["chunk_id"]),
                    "chunk_type": chunk_dict["chunk_type"],
                    "position_path": position_path,
                    "start_line": chunk_dict["start_line"],
                    "end_line": chunk_dict["end_line"],
                    "index_version": index_version,
                    "embedding_model": embedding_model,
                }
                self._qdrant_store.upsert_hybrid(
                    collection=collection_name,
                    point_id=chunk_dict["chunk_id"],
                    dense_vector=embeddings[i],
                    sparse_vector=sparse_vectors[i],
                    payload=payload,
                )
            logger.info(
                "Upserted %d hybrid points to Qdrant collection %s",
                len(chunk_dicts), collection_name,
            )

            # 11b. Extract graph relations (004, FR-001/FR-003): deterministic
            # hard edges from Java/DDL chunks + optional offline soft-relation
            # inference. Runs BEFORE publish so graph relations are ready when
            # the version becomes searchable (FR-013 readiness prerequisite).
            stage_start = datetime.now(timezone.utc)
            graph_details = await self._extract_graph_relations(
                source=source,
                redacted_text=redacted_text,
                chunk_dicts=chunk_dicts,
                scope_id=scope_id,
                version_number=version_number,
            )
            stages.append({
                "stage": "graph_relations",
                "status": "completed",
                "started_at": stage_start.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "details": graph_details,
            })

            # 11c. graph_ready user declaration gate (004, FR-013/FR-015):
            # a version declaring graph_ready is only publishable once its
            # graph relations are ready; the FR-015 capability implication is
            # validated before the flag is granted.
            if request_graph_ready:
                if int(graph_details.get("hard_edges_written", 0)) <= 0:
                    raise ValueError(
                        "graph_ready declared but graph relations are not ready "
                        "(no hard relations extracted); the version stays "
                        "non-searchable (FR-013)"
                    )
                from rag_mcp.graph.capabilities import validate_capabilities

                new_caps = dict(version.capabilities or {})
                new_caps["graph_ready"] = True
                validate_capabilities(new_caps)
                version.capabilities = new_caps
                version.graph_ready = True
                await self._session.flush()

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

    async def _read_raw_bytes(self, source: KnowledgeSource) -> bytes:
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

        content = file_path.read_bytes()
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
        self, content, fmt: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse content using the appropriate format parser (FR-009, 003 extends to 8 formats).

        Args:
            content: Credential-redacted text (str) for text formats, or raw
                bytes for binary formats (word, pdf).
            fmt: Format string ('markdown', 'java', 'openapi', 'ddl', 'go',
                'python', 'word', 'pdf').
            filename: Original filename for diagnostics.

        Returns:
            List of chunk dicts from the parser.

        Raises:
            ValueError: If the format is unsupported or parsing fails.
        """
        if fmt == "markdown":
            parser = MarkdownParser()
            return parser.parse(content)
        elif fmt == "java":
            parser = JavaParser()
            return parser.parse(content, filename=filename)
        elif fmt == "openapi":
            from rag_mcp.parsers.openapi_parser import OpenAPIParser
            return OpenAPIParser().parse(content, filename=filename)
        elif fmt == "ddl":
            from rag_mcp.parsers.ddl_parser import DDLParser
            return DDLParser().parse(content, filename=filename)
        elif fmt == "go":
            from rag_mcp.parsers.go_parser import GoParser
            return GoParser().parse(content, filename=filename)
        elif fmt == "python":
            from rag_mcp.parsers.python_parser import PythonParser
            return PythonParser().parse(content, filename=filename)
        elif fmt in ("word", "pdf"):
            # Binary formats: content is the extracted+redacted text string
            # (text_extraction stage already ran, preserving structure markers)
            if fmt == "word":
                from rag_mcp.parsers.word_parser import WordParser
                return WordParser().parse(content, filename=filename)
            else:
                from rag_mcp.parsers.pdf_parser import PDFParser
                return PDFParser().parse(content, filename=filename)
        else:
            raise ValueError(
                f"Unsupported format {fmt!r} for file {filename}. "
                f"Supported: markdown, java, openapi, ddl, go, python, word, pdf"
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

        004 (FR-015): the capability implication is validated defensively at
        publish time — graph_ready=true requires dense_ready AND lexical_ready.
        """
        from rag_mcp.graph.capabilities import validate_capabilities

        validate_capabilities(version.capabilities)

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
            collection = f"chunks_hybrid_{index_version}"
            try:
                self._qdrant_store.delete_points_by_version(collection, version_id)
            except Exception:  # noqa: BLE001 - Qdrant outage must not block PG cleanup
                logger.exception(
                    "Failed to purge Qdrant points for version %s (collection %s)",
                    version_id,
                    collection,
                )

        # 004 (FR-016): purge the superseded version's graph derived data —
        # expansion-path rows first (chunk FKs), then hard/soft relations for
        # the (scope, version_number) pair. Graph index_version equals the
        # version_number the relations were extracted for.
        from sqlalchemy import text as sa_text

        version_row = await self._session.execute(
            select(KnowledgeVersion.knowledge_scope_id,
                   KnowledgeVersion.version_number).where(
                KnowledgeVersion.version_id == version_id
            )
        )
        version_info = version_row.first()
        if version_info is not None:
            scope_id, version_number = version_info
            await self._session.execute(sa_text(
                "DELETE FROM graph_expansion_path WHERE chunk_id IN ("
                "SELECT chunk_id FROM chunks WHERE version_id = :vid)"
            ), {"vid": version_id})
            await self._session.execute(sa_text(
                "DELETE FROM soft_relation WHERE knowledge_scope_id = :ksid "
                "AND index_version = :iv"
            ), {"ksid": scope_id, "iv": version_number})
            await self._session.execute(sa_text(
                "DELETE FROM graph_edge WHERE knowledge_scope_id = :ksid "
                "AND index_version = :iv"
            ), {"ksid": scope_id, "iv": version_number})

        await self._session.execute(
            sa_delete(Chunk).where(Chunk.version_id == version_id)
        )

    # ------------------------------------------------------------------
    # 004: graph relation extraction at ingest (FR-001/FR-003)
    # ------------------------------------------------------------------

    async def _resolve_project_id(self, scope_id: int) -> int | None:
        """Resolve the owning project_id of a knowledge scope, if any.

        Graph edges require the full isolation triple
        (knowledge_scope_id, project_id, index_version). Scopes without a
        project (e.g. reserved public scopes) skip graph extraction.
        """
        from rag_mcp.models.project import Project

        result = await self._session.execute(
            select(Project.project_id).where(
                Project.knowledge_scope_id == scope_id
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def _extract_graph_relations(
        self,
        source: KnowledgeSource,
        redacted_text: str,
        chunk_dicts: list[dict[str, Any]],
        scope_id: int,
        version_number: int,
    ) -> dict[str, Any]:
        """Extract graph relations for the ingested source (004, FR-001/FR-003).

        Deterministic hard-relation extraction runs inside the ingestion flow:
        Java sources contribute calls/called_by edges (JavaCallGraphExtractor),
        DDL sources contribute fk_references/fk_referenced_by edges
        (DdlFkExtractor). When an offline soft-relation LLM is configured,
        SoftRelationInference materialises inferred relations with the five
        mandatory metadata.

        Degradation (Constitution III): AST/parse failures report a reason and
        produce zero edges rather than fabricating relations; they do not fail
        the ingestion.
        """
        from rag_mcp.graph.store.base import GraphScope
        from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore

        details: dict[str, Any] = {
            "format": source.format,
            "hard_edges_written": 0,
            "soft_relations_written": 0,
        }

        project_id = await self._resolve_project_id(scope_id)
        if project_id is None:
            details["skipped"] = "no_project_for_scope"
            logger.info(
                "Graph extraction skipped for scope %s: no owning project",
                scope_id,
            )
            return details

        scope = GraphScope(
            knowledge_scope_id=scope_id,
            project_id=project_id,
            index_version=version_number,
        )
        store = PostgresGraphStore(self._session)

        # 1) Deterministic hard-relation extraction (FR-001)
        if source.format in ("java", "ddl"):
            try:
                if source.format == "java":
                    from rag_mcp.graph.extractors.java_call_graph import (
                        JavaCallGraphExtractor,
                    )
                    extractor = JavaCallGraphExtractor()
                else:
                    from rag_mcp.graph.extractors.ddl_fk import DdlFkExtractor
                    extractor = DdlFkExtractor()

                edges = extractor.extract(redacted_text, chunk_dicts, scope)
                # Stamp the ingested version number onto every edge so the
                # isolation triple (scope, project, index_version) matches the
                # published version it belongs to.
                for edge in edges:
                    edge["version"] = version_number
                written = await store.write_edges(edges, scope)
                details["hard_edges_written"] = written
                logger.info(
                    "Graph hard relations extracted: source=%s format=%s edges=%d",
                    source.source_id, source.format, written,
                )
            except Exception as exc:  # noqa: BLE001 - degrade, never fabricate
                details["hard_degraded_reason"] = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Graph hard-relation extraction degraded for source %s: %s",
                    source.source_id, exc,
                )

        # 2) Offline soft-relation inference (FR-003; only with a configured LLM)
        if self._soft_relation_llm is not None:
            try:
                from rag_mcp.graph.soft_relation_inference import (
                    SoftRelationInference,
                )

                inference = SoftRelationInference()
                relations = inference.infer(
                    chunks=chunk_dicts,
                    scope=scope,
                    llm=self._soft_relation_llm,
                    model_and_version=self._soft_relation_model_and_version,
                    inference_source="llm-offline",
                    version=version_number,
                )
                for relation in relations:
                    self._session.add(relation)
                await self._session.flush()
                details["soft_relations_written"] = len(relations)
                logger.info(
                    "Soft relations inferred at ingest: source=%s count=%d",
                    source.source_id, len(relations),
                )
            except Exception as exc:  # noqa: BLE001 - degrade, never fabricate
                details["soft_degraded_reason"] = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Soft-relation inference degraded for source %s: %s",
                    source.source_id, exc,
                )
        else:
            details["soft_skipped"] = "no_llm_configured"

        return details
