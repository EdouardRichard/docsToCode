"""Unit tests for the 006 retrieval_run model extension (T009, RED first).

Asserts RetrievalRun gains the 006 runtime columns (data-model §4.1):
tool / instance_id / instance_mode / error_summary / trace_body_recorded /
provider_usage, and that query_text becomes nullable — while every 002/004/005
column (retrieval_mode / subpath_timings / evidence_ref_ids / format) stays
intact.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID


def test_new_columns_mapped() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    cols = RetrievalRun.__table__.columns
    for name in (
        "tool",
        "instance_id",
        "instance_mode",
        "error_summary",
        "trace_body_recorded",
        "provider_usage",
    ):
        assert name in cols, f"RetrievalRun.{name} missing (T010 not implemented)"


def test_tool_column_mapping() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    tool = RetrievalRun.__table__.columns["tool"]
    assert tool.nullable is False
    assert str(tool.server_default.arg).strip("'") == "search_knowledge"


def test_instance_columns_mapping() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    cols = RetrievalRun.__table__.columns
    assert isinstance(cols["instance_id"].type, UUID)
    assert cols["instance_id"].nullable is True
    assert cols["instance_mode"].nullable is True
    assert cols["instance_mode"].type.length == 8


def test_query_text_nullable() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    assert RetrievalRun.__table__.columns["query_text"].nullable is True


def test_trace_body_recorded_mapping() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    col = RetrievalRun.__table__.columns["trace_body_recorded"]
    assert col.nullable is False
    assert str(col.server_default.arg).strip("'").lower() in ("true", "1")


def test_jsonb_columns_mapping() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    cols = RetrievalRun.__table__.columns
    assert isinstance(cols["error_summary"].type, JSONB)
    assert cols["error_summary"].nullable is True
    assert isinstance(cols["provider_usage"].type, JSONB)
    assert cols["provider_usage"].nullable is True


def test_existing_columns_unchanged() -> None:
    """002/004/005 columns must not regress (T010 AC)."""
    from rag_mcp.models.retrieval_run import RetrievalRun

    cols = RetrievalRun.__table__.columns
    for name in (
        "run_id",
        "query_text",
        "project_scopes",
        "completion_status",
        "evidence_count",
        "duration_ms",
        "retrieval_mode",
        "subpath_timings",
        "evidence_ref_ids",
        "format",
        "created_at",
        "expires_at",
    ):
        assert name in cols, f"existing column {name} must remain"
    assert isinstance(cols["subpath_timings"].type, JSONB)
    assert isinstance(cols["evidence_ref_ids"].type, JSONB)
    assert isinstance(cols["created_at"].type, TIMESTAMP)


def test_aggregation_indexes() -> None:
    from rag_mcp.models.retrieval_run import RetrievalRun

    indexes = {idx.name: idx for idx in RetrievalRun.__table__.indexes}
    assert "idx_rr_mode_created" in indexes, "existing index must remain"

    mode_tool = [
        idx
        for idx in RetrievalRun.__table__.indexes
        if [c.name for c in idx.columns] == ["instance_mode", "tool", "created_at"]
    ]
    assert mode_tool, "(instance_mode, tool, created_at) index must be mapped"
    status_created = [
        idx
        for idx in RetrievalRun.__table__.indexes
        if [c.name for c in idx.columns] == ["completion_status", "created_at"]
    ]
    assert status_created, "(completion_status, created_at) index must be mapped"
