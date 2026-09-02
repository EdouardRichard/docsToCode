"""Standalone table definitions for 005 Agent orchestration runtime tables (T003).

This module provides SQLAlchemy Table definitions usable independently
of Alembic (e.g. for test setup or direct schema creation). The authoritative
migration is alembic/versions/0050_create_agentic_tables.py.

All four tables carry isolation (knowledge_scope_id, project_id, index_version)
and TTL columns (blueprint sec 20). Append-only: only INSERT by the store layer.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, ForeignKey, Index,
    Integer, MetaData, Numeric, Table, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

metadata = MetaData()

evidence_ledger_entry = Table(
    "evidence_ledger_entry", metadata,
    Column("ledger_entry_id", BigInteger, primary_key=True, comment="Snowflake ID"),
    Column("request_id", Text, nullable=False),
    Column("run_id", Text, nullable=False),
    Column("round_index", Integer, nullable=False),
    Column("sub_problem_id", Integer, nullable=False),
    Column("evidence_id", Text, nullable=False),
    Column("retrieval_query", Text, nullable=False),
    Column("retriever", Text, nullable=False),
    Column("score", Numeric(6, 4), nullable=False),
    Column("source_version", Integer, nullable=False),
    Column("source_position", Text, nullable=False),
    Column("knowledge_scope_id", BigInteger, nullable=False, comment="Isolation: scope"),
    Column("knowledge_scope_type", Text, nullable=False, comment="project or public"),
    Column("project_id", BigInteger, nullable=False, comment="Isolation: project"),
    Column("index_version", Integer, nullable=False, comment="Isolation: version"),
    Column("referenced_by_agent", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")),
    Column("ttl_expires_at", TIMESTAMP(timezone=True), nullable=True),
    CheckConstraint("retriever IN ('dense','sparse','graph','fusion','rerank')", name="chk_ledger_retriever"),
    CheckConstraint("referenced_by_agent IN ('query_planner','evidence_analyst','context_orchestrator')", name="chk_ledger_referenced_by_agent"),
    CheckConstraint("round_index >= 0", name="chk_ledger_round_index"),
    CheckConstraint("sub_problem_id >= 1", name="chk_ledger_sub_problem_id"),
    CheckConstraint("score >= 0 AND score <= 1", name="chk_ledger_score"),
    CheckConstraint("source_version >= 1", name="chk_ledger_source_version"),
    Index("idx_ledger_scope", "knowledge_scope_id", "project_id", "index_version", "created_at"),
    Index("idx_ledger_run", "run_id", "round_index", "sub_problem_id"),
    Index("idx_ledger_request_evidence", "request_id", "evidence_id"),
    comment="Append-only evidence ledger (005)",
)

agent_judgment = Table(
    "agent_judgment", metadata,
    Column("judgment_id", BigInteger, primary_key=True, comment="Snowflake ID"),
    Column("run_id", Text, nullable=False),
    Column("round_index", Integer, nullable=False),
    Column("coverage_state", Text, nullable=False),
    Column("conflict_type", Text, nullable=False),
    Column("uncovered_sub_problem_ids", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("needs_supplementary", Boolean, nullable=False),
    Column("gap_descriptions", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("model_and_version", Text, nullable=False),
    Column("schema_valid", Boolean, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")),
    CheckConstraint("coverage_state IN ('covered','partial','uncovered')", name="chk_judgment_coverage_state"),
    CheckConstraint("conflict_type IN ('none','version_conflict','source_conflict','domain_conflict')", name="chk_judgment_conflict_type"),
    CheckConstraint("round_index >= 0", name="chk_judgment_round_index"),
    Index("idx_judgment_run", "run_id", "round_index"),
    comment="Evidence analyst judgments (005)",
)

context_selection_list = Table(
    "context_selection_list", metadata,
    Column("context_result_id", Text, primary_key=True, comment="Context result identifier"),
    Column("run_id", Text, nullable=False),
    Column("ledger_entry_id", BigInteger, ForeignKey("evidence_ledger_entry.ledger_entry_id"), primary_key=True, comment="Selected/deduped/truncated entry"),
    Column("decision", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")),
    CheckConstraint("decision IN ('selected','truncated','deduped')", name="chk_selection_decision"),
    Index("idx_selection_run", "run_id"),
    comment="Append-only context selection list (005)",
)

agentic_retrieval_run = Table(
    "agentic_retrieval_run", metadata,
    Column("run_id", BigInteger, primary_key=True, comment="Snowflake ID"),
    Column("request_id", Text, nullable=False),
    Column("project_scope", JSONB, nullable=False),
    Column("knowledge_scope_ids", JSONB, nullable=False),
    Column("task_context", JSONB, nullable=True),
    Column("run_config", JSONB, nullable=False),
    Column("completion_status", Text, nullable=False),
    Column("max_rounds", Integer, nullable=False, server_default=text("2")),
    Column("rounds_completed", Integer, nullable=False),
    Column("guardrail_state", JSONB, nullable=False),
    Column("sub_path_timings", JSONB, nullable=False),
    Column("agent_outputs_ref", JSONB, nullable=False),
    Column("ledger_ref", JSONB, nullable=False),
    Column("total_cost", Numeric(10, 4), nullable=True, comment="LLM cost (SC-007)"),
    Column("schema_valid_all", Boolean, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")),
    Column("ttl_expires_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW() + INTERVAL '7 days'"), comment="TTL expiry"),
    CheckConstraint("completion_status IN ('complete','partial','no_evidence','failed')", name="chk_agentic_run_completion_status"),
    CheckConstraint("max_rounds >= 1 AND max_rounds <= 3", name="chk_agentic_run_max_rounds"),
    CheckConstraint("rounds_completed >= 0", name="chk_agentic_run_rounds_completed"),
    Index("idx_run_request", "request_id"),
    Index("idx_run_scope", "knowledge_scope_ids", "created_at"),
    comment="Agent orchestration retrieval run (005)",
)

TABLE_NAMES = ["evidence_ledger_entry", "agent_judgment", "context_selection_list", "agentic_retrieval_run"]
APPEND_ONLY_TABLES = {"evidence_ledger_entry", "context_selection_list"}
TTL_TABLES = {"evidence_ledger_entry", "agentic_retrieval_run"}
ISOLATION_TABLES = {"evidence_ledger_entry"}
