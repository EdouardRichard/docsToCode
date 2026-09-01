#!/usr/bin/env python3
"""Rebuild Qdrant hybrid vectors for eval-dataset scopes from PG chunks (004).

Derived-data rebuild per blueprint §8.4 / FR-016: when the shared Qdrant
loses the eval corpus vectors (e.g. collection recreated), this tool
reconstructs the hybrid points from the persisted PostgreSQL chunks — same
chunk_ids, so eval_dataset.json expected_evidence_ids stay valid.

The BM25 sparse encoder is fitted on ALL published chunk texts ordered by
chunk_id, matching the query-time fit used by eval/run_eval.py and
eval/run_graph_comparison.py so sparse term IDs stay consistent.

Usage:
    python eval/reindex_eval_qdrant.py --dataset eval/eval_dataset.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from qdrant_client.models import PointStruct
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider
from rag_mcp.services.ingestion_service import _derive_index_version

logger = logging.getLogger(__name__)

_EMBED_BATCH = 16


async def reindex(dataset_path: str) -> int:
    settings = get_settings()
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    scope_ids = sorted({
        int(s) for entry in dataset for s in entry.get("project_scope", [])
    })
    logger.info("Dataset scopes: %d", len(scope_ids))

    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Fit the sparse encoder exactly like the eval runners do (all published
    # chunk texts ordered by chunk_id) so stored sparse IDs match queries.
    async with session_factory() as session:
        all_texts = (await session.execute(
            sa_select(Chunk.content_text)
            .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
            .where(KnowledgeVersion.status == "published")
            .order_by(Chunk.chunk_id)
        )).scalars().all()
    if not all_texts:
        logger.error("No published chunks found; nothing to fit")
        await engine.dispose()
        return 1
    encoder = BM25SparseEncoder()
    encoder.fit(list(all_texts))
    logger.info("Sparse encoder fitted on %d texts", len(all_texts))

    # Collect chunks of the dataset scopes' published versions
    chunks: list[Chunk] = []
    async with session_factory() as session:
        for scope_id in scope_ids:
            version = (await session.execute(
                sa_select(KnowledgeVersion).where(
                    KnowledgeVersion.knowledge_scope_id == scope_id,
                    KnowledgeVersion.status == "published",
                ).order_by(KnowledgeVersion.version_number.desc())
            )).scalars().first()
            if version is None:
                logger.warning("Scope %s: no published version, skipped", scope_id)
                continue
            rows = (await session.execute(
                sa_select(Chunk).where(
                    Chunk.knowledge_scope_id == scope_id,
                    Chunk.version_id == version.version_id,
                ).order_by(Chunk.chunk_id)
            )).scalars().all()
            chunks.extend(rows)
    if not chunks:
        logger.error("No chunks found for dataset scopes")
        await engine.dispose()
        return 1
    logger.info("Reindexing %d chunks across dataset scopes", len(chunks))

    provider = LocalCPUEmbeddingProvider(settings.embedding_model)
    store = QdrantStore(url=settings.qdrant_url)
    index_version = _derive_index_version(settings.embedding_model)
    collection = f"chunks_hybrid_{index_version}"
    dense_collection = f"chunks_dense_{index_version}"
    dimension = provider.get_dimension()
    if not store.collection_exists(collection):
        logger.info("Creating hybrid collection %s", collection)
        store.create_hybrid_collection(collection, dimension)
    if not store.collection_exists(dense_collection):
        logger.info("Creating dense collection %s", dense_collection)
        store.create_collection(dense_collection, dimension)

    upserted = 0
    for start in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[start:start + _EMBED_BATCH]
        texts = [c.content_text or "" for c in batch]
        dense_vectors = await provider.embed_texts(texts)
        for chunk, dense in zip(batch, dense_vectors):
            position_path = chunk.position_path or ""
            payload: dict[str, Any] = {
                "knowledge_scope_id": str(chunk.knowledge_scope_id),
                "source_id": str(chunk.source_id),
                "version_id": str(chunk.version_id),
                "chunk_id": str(chunk.chunk_id),
                "chunk_type": chunk.chunk_type,
                "position_path": position_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "index_version": index_version,
                "embedding_model": settings.embedding_model,
            }
            store.upsert_hybrid(
                collection=collection,
                point_id=chunk.chunk_id,
                dense_vector=dense,
                sparse_vector=encoder.encode(chunk.content_text or ""),
                payload=payload,
            )
            store.upsert_points(
                dense_collection,
                [PointStruct(id=chunk.chunk_id, vector=dense, payload=payload)],
            )
            upserted += 1
        logger.info("  upserted %d/%d", upserted, len(chunks))

    logger.info(
        "Reindex complete: %d points in %s and %s",
        upserted, collection, dense_collection,
    )
    await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild eval-corpus Qdrant vectors from PG chunks (FR-016)."
    )
    parser.add_argument("--dataset", "-d", default="eval/eval_dataset.json")
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(reindex(args.dataset))


if __name__ == "__main__":
    sys.exit(main())
