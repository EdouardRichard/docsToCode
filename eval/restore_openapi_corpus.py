#!/usr/bin/env python3
"""Restore the openapi eval corpus (003 T055, FR-024).

The fixed eval dataset's two openapi queries reference a project knowledge
scope (352014591405850625) whose data was removed from the shared
development database after 003 acceptance. This tool re-publishes the
corpus from the committed fixture (backend/tests/fixtures/samples/
openapi.json) through the real redact+parse pipeline, pinning the chunk
IDs declared in eval/eval_dataset.json so the fixed acceptance set stays
valid:

  - 352014592559284226 -> endpoint "GET /api/v1/users"
  - 352014592559284224 -> schema  "components.schemas.User"

Idempotent: exits 0 without writing when the expected chunks are already
present in a published version. After restoring, rebuild the Qdrant
vectors with:

    python eval/reindex_eval_qdrant.py --dataset eval/eval_dataset.json
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from rag_mcp.config import get_settings
from rag_mcp.models import Chunk, KnowledgeScope, KnowledgeVersion, Project
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.parsers.credential_redactor import redact_credentials
from rag_mcp.parsers.openapi_parser import OpenAPIParser
from rag_mcp.services.ingestion_service import _derive_index_version
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

SCOPE_ID = 352014591405850625
SCOPE_NAME = "003 openapi"
FIXTURE = _REPO_ROOT / "backend" / "tests" / "fixtures" / "samples" / "openapi.json"

# Chunk IDs pinned to eval/eval_dataset.json expected_evidence_ids
PINNED_CHUNK_IDS = {
    "GET /api/v1/users": 352014592559284226,
    "schema:components.schemas.User": 352014592559284224,
}


async def _already_restored(session: AsyncSession) -> bool:
    expected = set(PINNED_CHUNK_IDS.values())
    rows = (await session.execute(
        select(Chunk.chunk_id, KnowledgeVersion.status)
        .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
        .where(Chunk.chunk_id.in_(list(expected)))
    )).all()
    published = {row[0] for row in rows if row[1] == "published"}
    return expected.issubset(published)


async def restore() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        if await _already_restored(session):
            logger.info("openapi eval corpus already restored; nothing to do")
            await engine.dispose()
            return 0

        existing_scope = (await session.execute(
            select(KnowledgeScope).where(KnowledgeScope.scope_id == SCOPE_ID)
        )).scalar_one_or_none()
        if existing_scope is not None:
            logger.error(
                "Scope %s exists but the expected chunks are missing; refusing "
                "to half-modify an existing scope. Inspect it manually.", SCOPE_ID,
            )
            await engine.dispose()
            return 1

        raw_bytes = FIXTURE.read_bytes()
        text_content = raw_bytes.decode("utf-8")
        redacted_text = redact_credentials(text_content)
        chunk_dicts = OpenAPIParser().parse(redacted_text, "openapi.json")
        if not chunk_dicts:
            logger.error("openapi fixture produced no chunks")
            await engine.dispose()
            return 1

        # Pin the expected chunk IDs (eval dataset compatibility)
        by_path = {c.get("structure_path"): c for c in chunk_dicts}
        for path, chunk_id in PINNED_CHUNK_IDS.items():
            if path not in by_path:
                logger.error("fixture does not contain expected chunk %r", path)
                await engine.dispose()
                return 1
            by_path[path]["chunk_id"] = chunk_id
        for chunk_dict in chunk_dicts:
            chunk_dict.setdefault("chunk_id", generate_id())

        now = datetime.now(timezone.utc)
        embedding_model = settings.embedding_model
        index_version = _derive_index_version(embedding_model)

        scope = KnowledgeScope(
            scope_id=SCOPE_ID, scope_type="project",
            name=SCOPE_NAME, status="active",
            created_at=now, updated_at=now,
        )
        project = Project(
            project_id=generate_id(), name=SCOPE_NAME,
            knowledge_scope_id=SCOPE_ID,
        )
        source = KnowledgeSource(
            source_id=generate_id(),
            knowledge_scope_id=SCOPE_ID,
            filename="openapi.json",
            content_hash=hashlib.sha256(raw_bytes).hexdigest(),
            format="openapi",
            size_bytes=len(raw_bytes),
            status="published",
            created_at=now, updated_at=now,
        )
        version = KnowledgeVersion(
            version_id=generate_id(),
            knowledge_scope_id=SCOPE_ID,
            version_number=1,
            capabilities={"dense_ready": True, "lexical_ready": True},
            status="published",
            published_at=now,
            created_at=now,
        )
        session.add_all([scope, project, source, version])
        await session.flush()

        chunk_records = []
        for chunk_dict in chunk_dicts:
            position_path = (
                chunk_dict.get("section_path")
                or chunk_dict.get("symbol_path")
                or chunk_dict.get("structure_path")
                or ""
            )
            chunk_records.append(Chunk(
                chunk_id=chunk_dict["chunk_id"],
                source_id=source.source_id,
                version_id=version.version_id,
                knowledge_scope_id=SCOPE_ID,
                parent_chunk_id=chunk_dict.get("parent_chunk_id"),
                content_text=chunk_dict["content_text"],
                position_path=position_path,
                chunk_type=chunk_dict["chunk_type"],
                start_line=chunk_dict["start_line"],
                end_line=chunk_dict["end_line"],
                token_count=chunk_dict.get("token_count") or 1,
                embedding_model=embedding_model,
                index_version=index_version,
            ))
        session.add_all(chunk_records)
        await session.commit()

        logger.info(
            "Restored openapi eval corpus: scope=%s version=%d chunks=%d "
            "(pinned endpoint/schema chunk IDs %s)",
            SCOPE_ID, version.version_number, len(chunk_records),
            sorted(PINNED_CHUNK_IDS.values()),
        )
        logger.info(
            "Next: rebuild vectors with "
            "`python eval/reindex_eval_qdrant.py --dataset eval/eval_dataset.json`",
        )

    await engine.dispose()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    return asyncio.run(restore())


if __name__ == "__main__":
    sys.exit(main())