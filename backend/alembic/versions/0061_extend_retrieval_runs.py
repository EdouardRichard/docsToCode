"""extend_retrieval_runs

Revision ID: 0061
Revises: 0060
Create Date: 2026-09-10 02:10:00.000000

006 Runtime Hardening: extends retrieval_runs with the runtime columns
(data-model.md §4.1, all backward compatible):

  - tool                  : 'search_knowledge' (default) | 'get_evidence'
  - instance_id / instance_mode : instance attribution (NULL for legacy rows)
  - error_summary         : {code, message, failed_paths[]} (FR-020)
  - trace_body_recorded   : whether the query body was recorded (default TRUE)
  - provider_usage        : per-request provider call/char counters (FR-016)
  - query_text            : NOT NULL -> NULLABLE (FR-018 trace-body switch)

Legacy rows are backfilled by the server defaults: tool='search_knowledge',
trace_body_recorded=TRUE, instance_id/instance_mode NULL, query_text keeps
its original value (data-model §4.2). Adds the aggregation composite indexes
(instance_mode, tool, created_at) and (completion_status, created_at).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0061'
down_revision: Union[str, Sequence[str], None] = '0060'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('retrieval_runs', sa.Column(
        'tool', sa.String(length=16), nullable=False,
        server_default=sa.text("'search_knowledge'"),
        comment="'search_knowledge' or 'get_evidence' (FR-016 by-Tool metrics)"))
    op.add_column('retrieval_runs', sa.Column(
        'instance_id', postgresql.UUID(as_uuid=True), nullable=True,
        comment='Serving instance (NULL for pre-006 rows)'))
    op.add_column('retrieval_runs', sa.Column(
        'instance_mode', sa.String(length=8), nullable=True,
        comment='Instance mode redundancy (survives registry row purge)'))
    op.add_column('retrieval_runs', sa.Column(
        'error_summary', postgresql.JSONB(), nullable=True,
        comment='{code, message, failed_paths[]} (FR-020 error backtrace)'))
    op.add_column('retrieval_runs', sa.Column(
        'trace_body_recorded', sa.Boolean(), nullable=False,
        server_default=sa.text('TRUE'),
        comment='Whether the query body was recorded for this row'))
    op.add_column('retrieval_runs', sa.Column(
        'provider_usage', postgresql.JSONB(), nullable=True,
        comment='{embedding_calls, rerank_calls, llm_calls, llm_prompt_chars, llm_completion_chars}'))

    # query_text NOT NULL -> NULLABLE (FR-018: TRACE_BODY_ENABLED=false)
    op.alter_column('retrieval_runs', 'query_text',
                    existing_type=sa.Text(), nullable=True)

    op.create_check_constraint(
        'chk_rr_tool', 'retrieval_runs',
        "tool IN ('search_knowledge', 'get_evidence')")
    op.create_check_constraint(
        'chk_rr_instance_mode', 'retrieval_runs',
        "instance_mode IS NULL OR instance_mode IN ('writer', 'reader')")

    # Aggregation composite indexes (data-model §6, SC-006 second-level reads)
    op.create_index('idx_rr_instance_tool_created', 'retrieval_runs',
                    ['instance_mode', 'tool', 'created_at'])
    op.create_index('idx_rr_status_created', 'retrieval_runs',
                    ['completion_status', 'created_at'])


def downgrade() -> None:
    op.drop_index('idx_rr_status_created', table_name='retrieval_runs')
    op.drop_index('idx_rr_instance_tool_created', table_name='retrieval_runs')
    op.drop_constraint('chk_rr_instance_mode', 'retrieval_runs', type_='check')
    op.drop_constraint('chk_rr_tool', 'retrieval_runs', type_='check')
    # Restore NOT NULL: backfill NULL bodies with empty string first.
    op.execute("UPDATE retrieval_runs SET query_text = '' WHERE query_text IS NULL")
    op.alter_column('retrieval_runs', 'query_text',
                    existing_type=sa.Text(), nullable=False)
    op.drop_column('retrieval_runs', 'provider_usage')
    op.drop_column('retrieval_runs', 'trace_body_recorded')
    op.drop_column('retrieval_runs', 'error_summary')
    op.drop_column('retrieval_runs', 'instance_mode')
    op.drop_column('retrieval_runs', 'instance_id')
    op.drop_column('retrieval_runs', 'tool')
