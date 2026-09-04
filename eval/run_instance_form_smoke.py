#!/usr/bin/env python3
"""Regenerate the instance-form smoke comparison report (006 T070/T087).

Runs the 001 baseline 11 queries through both instance forms (writer +
reader) and persists eval/instance_form_smoke_report.json with the current
code, which includes the T086 gate fields (pass + tolerance_semantics).

Usage:
    python eval/run_instance_form_smoke.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_mcp.config import get_settings
from rag_mcp.eval.instance_form_smoke import (
    load_baseline_queries,
    run_form_smoke,
    write_instance_form_report,
)
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider

logger = logging.getLogger(__name__)


async def main() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    qdrant_store = QdrantStore()
    embedding_provider = LocalCPUEmbeddingProvider()
    queries = load_baseline_queries()

    reports = {}
    for mode in ("writer", "reader"):
        report = await run_form_smoke(
            mode,
            session_factory=session_factory,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            queries=queries,
            top_k=5,
            tolerance=0.01,
        )
        reports[mode] = report
        logger.info("%s: pass=%s means=%s", mode, report["pass"], report["means"])

    combined = {
        "report_type": "instance_form_smoke",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instance_forms": reports,
    }
    out = write_instance_form_report(combined)
    logger.info("Instance-form smoke report written to %s", out)
    await engine.dispose()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    sys.exit(asyncio.run(main()))