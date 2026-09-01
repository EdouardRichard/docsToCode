import pytest
from rag_mcp.models.retrieval_run import RetrievalRun
from rag_mcp.utils.snowflake import generate_id
from datetime import datetime, timezone

class TestRetrievalRunFormat:
    def test_format_column_exists(self):
        assert hasattr(RetrievalRun, "format")

    async def test_format_nullable(self, db_session):
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test query",
            project_scopes=["123"],
            completion_status="complete",
            evidence_count=1,
            duration_ms=10,
            retrieval_mode="dense",
            evidence_ref_ids=[],
            format=None,
        )
        db_session.add(run)
        await db_session.flush()
        assert run.format is None

    @pytest.mark.parametrize("fmt", ["markdown","java","openapi","ddl","go","python","word","pdf"])
    async def test_format_accepts_all_8_values(self, db_session, fmt):
        run = RetrievalRun(
            run_id=generate_id(),
            query_text="test",
            project_scopes=["123"],
            completion_status="complete",
            evidence_count=1,
            duration_ms=10,
            retrieval_mode="dense",
            evidence_ref_ids=[],
            format=fmt,
        )
        db_session.add(run)
        await db_session.flush()
        assert run.format == fmt