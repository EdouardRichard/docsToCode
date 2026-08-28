"""Unit tests for RetrievalRun hybrid fields (T009).

Tests: retrieval_mode, subpath_timings, evidence_ref_ids; dense backward-compat;
hybrid requires subpath_timings.

These tests MUST FAIL before the model is extended with hybrid fields (TDD).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.retrieval_run import RetrievalRun
from rag_mcp.utils.snowflake import generate_id


# ---------------------------------------------------------------------------
# Dense backward compatibility
# ---------------------------------------------------------------------------

class TestDenseBackwardCompat:
    """Dense (001) RetrievalRun records must remain backward compatible."""

    @pytest.mark.asyncio
    async def test_dense_default_retrieval_mode(self, db_session: AsyncSession):
        """A RetrievalRun created without retrieval_mode must default to 'dense'."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test query",
            project_scopes=["123"],
            completion_status="complete",
            evidence_count=1,
            duration_ms=42,
        )
        db_session.add(run)
        await db_session.flush()

        # Reload to check server defaults
        await db_session.refresh(run)
        assert run.retrieval_mode == "dense", (
            "Default retrieval_mode must be 'dense' for backward compatibility"
        )

    @pytest.mark.asyncio
    async def test_dense_subpath_timings_null(self, db_session: AsyncSession):
        """Dense mode RetrievalRun should have subpath_timings = NULL."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test query",
            project_scopes=["123"],
            completion_status="complete",
            evidence_count=1,
            duration_ms=42,
        )
        db_session.add(run)
        await db_session.flush()
        await db_session.refresh(run)
        assert run.subpath_timings is None, (
            "Dense mode should have NULL subpath_timings (backward compat with 001)"
        )

    @pytest.mark.asyncio
    async def test_dense_evidence_ref_ids_default_empty(self, db_session: AsyncSession):
        """Dense mode RetrievalRun should have evidence_ref_ids = [] by default."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test query",
            project_scopes=["123"],
            completion_status="complete",
            evidence_count=1,
            duration_ms=42,
        )
        db_session.add(run)
        await db_session.flush()
        await db_session.refresh(run)
        assert run.evidence_ref_ids == [], (
            "Default evidence_ref_ids must be empty list"
        )


# ---------------------------------------------------------------------------
# Hybrid mode fields
# ---------------------------------------------------------------------------

class TestHybridFields:
    """Hybrid RetrievalRun records with subpath_timings and evidence_ref_ids."""

    @pytest.mark.asyncio
    async def test_hybrid_with_subpath_timings(self, db_session: AsyncSession):
        """Hybrid mode RetrievalRun with subpath_timings should persist."""
        timings = {
            "dense_recall_ms": 12.3,
            "sparse_recall_ms": 8.1,
            "fusion_ms": 0.4,
            "rerank_ms": 245.7,
            "total_ms": 266.5,
        }
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="hybrid query",
            project_scopes=["456"],
            completion_status="complete",
            evidence_count=3,
            duration_ms=267,
            retrieval_mode="hybrid",
            subpath_timings=timings,
            evidence_ref_ids=["100", "200", "300"],
        )
        db_session.add(run)
        await db_session.flush()
        await db_session.refresh(run)
        assert run.retrieval_mode == "hybrid"
        assert run.subpath_timings is not None
        assert run.subpath_timings["dense_recall_ms"] == 12.3
        assert run.subpath_timings["rerank_ms"] == 245.7
        assert run.evidence_ref_ids == ["100", "200", "300"]

    @pytest.mark.asyncio
    async def test_hybrid_requires_subpath_timings(self, db_session: AsyncSession):
        """Hybrid mode without subpath_timings must violate the CHECK constraint."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="hybrid query",
            project_scopes=["456"],
            completion_status="complete",
            evidence_count=1,
            duration_ms=10,
            retrieval_mode="hybrid",
            subpath_timings=None,  # Missing → should fail CHECK
        )
        db_session.add(run)
        with pytest.raises(Exception, match=".*chk_hybrid_timings.*|.*constraint.*|.*violat.*"):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_partial_with_failed_path(self, db_session: AsyncSession):
        """Partial status in hybrid mode should record subpath_timings."""
        timings = {
            "dense_recall_ms": 12.0,
            "sparse_recall_ms": 0.0,
            "fusion_ms": 0.1,
            "rerank_ms": 0.0,
            "total_ms": 12.1,
        }
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="partial query",
            project_scopes=["789"],
            completion_status="partial",
            evidence_count=2,
            duration_ms=12,
            retrieval_mode="hybrid",
            subpath_timings=timings,
            evidence_ref_ids=["100", "200"],
        )
        db_session.add(run)
        await db_session.flush()
        await db_session.refresh(run)
        assert run.completion_status == "partial"
        assert run.retrieval_mode == "hybrid"


# ---------------------------------------------------------------------------
# Evidence ref IDs
# ---------------------------------------------------------------------------

class TestEvidenceRefIds:
    """evidence_ref_ids tracks returned evidence for problem tracing."""

    @pytest.mark.asyncio
    async def test_evidence_ref_ids_stored(self, db_session: AsyncSession):
        """evidence_ref_ids should be stored as a JSONB array."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test",
            project_scopes=["1"],
            completion_status="complete",
            evidence_count=2,
            duration_ms=5,
            evidence_ref_ids=["111", "222"],
        )
        db_session.add(run)
        await db_session.flush()
        await db_session.refresh(run)
        assert run.evidence_ref_ids == ["111", "222"]

    @pytest.mark.asyncio
    async def test_evidence_ref_ids_empty_default(self, db_session: AsyncSession):
        """evidence_ref_ids should default to empty list."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test",
            project_scopes=["1"],
            completion_status="no_evidence",
            evidence_count=0,
            duration_ms=5,
        )
        db_session.add(run)
        await db_session.flush()
        await db_session.refresh(run)
        assert run.evidence_ref_ids == []


# ---------------------------------------------------------------------------
# CHECK constraint: retrieval_mode IN ('dense', 'hybrid')
# ---------------------------------------------------------------------------

class TestRetrievalModeCheck:
    """retrieval_mode must be 'dense' or 'hybrid' (CHECK constraint)."""

    @pytest.mark.asyncio
    async def test_dense_mode_valid(self, db_session: AsyncSession):
        """'dense' is a valid retrieval_mode."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test",
            project_scopes=["1"],
            completion_status="complete",
            evidence_count=0,
            duration_ms=5,
            retrieval_mode="dense",
        )
        db_session.add(run)
        await db_session.flush()
        assert run.retrieval_mode == "dense"

    @pytest.mark.asyncio
    async def test_invalid_mode_rejected(self, db_session: AsyncSession):
        """Invalid retrieval_mode must be rejected by CHECK constraint."""
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test",
            project_scopes=["1"],
            completion_status="complete",
            evidence_count=0,
            duration_ms=5,
            retrieval_mode="invalid_mode",
        )
        db_session.add(run)
        with pytest.raises(Exception):
            await db_session.flush()
