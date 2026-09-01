"""Unit test for migration 0045 (004, T043 support).

Validates retrieval_runs.retrieval_mode accepts 'graph_enhanced' and that
the timings requirement covers both hybrid and graph_enhanced modes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_retrieval_mode_check_accepts_graph_enhanced(db_session):
    row = (await db_session.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'chk_retrieval_mode'"
    ))).scalar_one()
    assert "graph_enhanced" in row


@pytest.mark.asyncio
async def test_hybrid_timings_check_covers_graph_enhanced(db_session):
    row = (await db_session.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'chk_hybrid_timings'"
    ))).scalar_one()
    assert "graph_enhanced" in row
