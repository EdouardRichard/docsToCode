"""Runtime metrics endpoint (006, T062).

GET /runtime/metrics — a read-only aggregation endpoint exposed only on the
writer management plane (readers never start the management plane, FR-004).
The response is aggregated numbers + identifiers conforming to
runtime-metrics.schema.json and contains NO query/evidence body (FR-017).
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.db import get_session_factory
from rag_mcp.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


@router.get("/runtime/metrics")
async def runtime_metrics(session: AsyncSession = Depends(get_session)) -> dict:
    """Query-time runtime metrics readout (FR-016/FR-017, SC-006)."""
    from rag_mcp.runtime.metrics import build_runtime_metrics

    settings = get_settings()
    return await build_runtime_metrics(session, settings)
