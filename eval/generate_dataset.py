#!/usr/bin/env python3
"""Generate evaluation dataset from ingested knowledge sources.

T052 — FR-024 compliance: produces a fixed evaluation dataset in JSON format
containing query, project_scope, and expected_evidence_ids fields.  The initial
version uses heuristic query generation (no LLM) based on chunk metadata;
queries are intended for human review and editing before baseline evaluation.

Usage:
    python eval/generate_dataset.py --output eval/eval_dataset.json
    python eval/generate_dataset.py --output eval/eval_dataset.json --db-url postgresql+asyncpg://...
    python eval/generate_dataset.py --output eval/eval_dataset.json --max-queries 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure backend source is importable when running from repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from rag_mcp.config import get_settings
from rag_mcp.models import Chunk, KnowledgeScope, KnowledgeVersion, Project

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query templates
# ---------------------------------------------------------------------------

_SECTION_TEMPLATES = [
    "What does {path} describe?",
    "Explain the content of {path}.",
    "Show me the section {path}.",
    "What information is covered in {path}?",
    "Describe the purpose of {path}.",
]

_SYMBOL_TEMPLATES = [
    "Show me the implementation of {path}.",
    "What does {path} do?",
    "Explain the code at {path}.",
    "Find the definition of {path}.",
    "What is the purpose of {path}?",
]


def _generate_query_for_chunk(chunk: Chunk) -> str:
    """Generate a natural-language query targeting a specific chunk.

    Uses simple heuristic templates based on chunk_type and position_path.
    """
    path = chunk.position_path or "unknown"

    if chunk.chunk_type == "symbol":
        templates = _SYMBOL_TEMPLATES
    else:
        templates = _SECTION_TEMPLATES

    # Deterministic template selection based on chunk_id for reproducibility
    template_idx = chunk.chunk_id % len(templates)
    template = templates[template_idx]

    return template.format(path=path)


# ---------------------------------------------------------------------------
# Dataset generation
# ---------------------------------------------------------------------------

async def generate_dataset(
    db_url: str,
    max_queries: int | None = None,
    scope_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Query the database for published chunks and generate eval entries.

    Args:
        db_url: Async PostgreSQL connection URL.
        max_queries: Optional cap on total generated queries.
        scope_ids: Optional filter to specific knowledge scope IDs.

    Returns:
        List of eval dataset entries.
    """
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    dataset: list[dict[str, Any]] = []

    try:
        async with async_session() as session:
            # Build base query: only chunks from published versions
            stmt = (
                select(Chunk)
                .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
                .where(KnowledgeVersion.status == "published")
            )

            if scope_ids:
                stmt = stmt.where(Chunk.knowledge_scope_id.in_(scope_ids))

            # Order deterministically for reproducibility
            stmt = stmt.order_by(Chunk.chunk_id)

            if max_queries:
                stmt = stmt.limit(max_queries)

            result = await session.execute(stmt)
            chunks = result.scalars().all()

            logger.info("Found %d published chunks for dataset generation", len(chunks))

            if not chunks:
                logger.warning(
                    "No published chunks found. Ensure knowledge sources have been "
                    "ingested and versions published before generating the dataset."
                )
                return []

            # Resolve scope_id → project mapping for project_scope field
            scope_to_project: dict[int, str] = {}
            unique_scope_ids = list({c.knowledge_scope_id for c in chunks})
            if unique_scope_ids:
                proj_stmt = (
                    select(Project.project_id, Project.name, KnowledgeScope.scope_id)
                    .join(KnowledgeScope, Project.knowledge_scope_id == KnowledgeScope.scope_id)
                    .where(KnowledgeScope.scope_id.in_(unique_scope_ids))
                )
                proj_result = await session.execute(proj_stmt)
                for row in proj_result.all():
                    # Use scope_id as string for JSON compatibility (snowflake IDs are large)
                    scope_to_project[row.scope_id] = str(row.scope_id)

            for chunk in chunks:
                query = _generate_query_for_chunk(chunk)
                scope_id_str = str(chunk.knowledge_scope_id)

                entry: dict[str, Any] = {
                    "query": query,
                    "project_scope": [scope_id_str],
                    "expected_evidence_ids": [str(chunk.chunk_id)],
                    # Metadata for human reviewers (not used by eval runner)
                    "_meta": {
                        "chunk_id": str(chunk.chunk_id),
                        "position_path": chunk.position_path,
                        "chunk_type": chunk.chunk_type,
                        "source_id": str(chunk.source_id),
                        "version_id": str(chunk.version_id),
                        "knowledge_scope_id": scope_id_str,
                        "review_status": "auto-generated",
                        "review_notes": "TODO: Review and refine this query for quality.",
                    },
                }
                dataset.append(entry)

    finally:
        await engine.dispose()

    return dataset


def _strip_meta(dataset: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of the dataset without _meta fields (clean output)."""
    return [
        {k: v for k, v in entry.items() if k != "_meta"}
        for entry in dataset
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate evaluation dataset from ingested knowledge sources (FR-024).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python eval/generate_dataset.py --output eval/eval_dataset.json\n"
            "  python eval/generate_dataset.py --output eval/eval_dataset.json --max-queries 100\n"
            "  python eval/generate_dataset.py --output eval/eval_dataset.json --keep-meta\n"
        ),
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        type=str,
        help="Output path for the JSON dataset file.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Async database URL. Defaults to DATABASE_URL env var or config default.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Maximum number of queries to generate (default: unlimited).",
    )
    parser.add_argument(
        "--scope-ids",
        type=str,
        default=None,
        help="Comma-separated knowledge scope IDs to filter by.",
    )
    parser.add_argument(
        "--keep-meta",
        action="store_true",
        default=False,
        help="Keep _meta fields in output (useful for human review phase).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = get_settings()
    db_url = args.db_url or settings.database_url

    scope_ids: list[int] | None = None
    if args.scope_ids:
        scope_ids = [int(s.strip()) for s in args.scope_ids.split(",")]

    logger.info("Generating evaluation dataset...")
    logger.info("  Database: %s", db_url.split("@")[-1] if "@" in db_url else db_url)
    logger.info("  Max queries: %s", args.max_queries or "unlimited")
    logger.info("  Scope filter: %s", scope_ids or "all")

    try:
        dataset = await generate_dataset(
            db_url=db_url,
            max_queries=args.max_queries,
            scope_ids=scope_ids,
        )
    except Exception as exc:
        logger.error("Failed to generate dataset: %s", exc, exc_info=True)
        return 1

    if not dataset:
        logger.warning("Generated dataset is empty. No published chunks found.")
        # Still write empty file so downstream tools don't break
        dataset = []

    # Optionally strip _meta
    output_data = dataset if args.keep_meta else _strip_meta(dataset)

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info(
        "Dataset written to %s (%d entries)",
        output_path, len(output_data),
    )

    if args.keep_meta:
        logger.info(
            "NOTE: _meta fields included. Review and edit queries before running eval. "
            "Set review_status to 'reviewed' after manual validation."
        )
    else:
        logger.info(
            "TIP: Re-run with --keep-meta to include metadata for human review."
        )

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
