"""create_agentic_tables

Revision ID: 0050
Revises: 0045
Create Date: 2026-09-02 02:00:00.000000

005 Agentic Retrieval Orchestration: creates four runtime tables for the
Agent orchestration path (data-model.md sections 2-5):

  - evidence_ledger_entry  : append-only evidence ledger (FR-008/FR-009)
  - agent_judgment          : evidence analyst judgments (FR-013/FR-015)
  - context_selection_list  : append-only selection list (FR-017)
  - agentic_retrieval_run   : run record + state envelope (FR-010/FR-031)

All tables carry the isolation triple (knowledge_scope_id, project_id,
index_version) and TTL expires_at columns (blueprint sec 20).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0050'
down_revision: Union[str, Sequence[str], None] = '0045'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. evidence_ledger_entry (append-only evidence ledger)
    op.create_table(
        'evidence_ledger_entry',
        sa.Column('ledger_entry_id', sa.BigInteger(), nullable=False,
                  comment='Snowflake ID'),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('round_index', sa.Integer(), nullable=False),
        sa.Column('sub_problem_id', sa.Integer(), nullable=False),
        sa.Column('evidence_id', sa.Text(), nullable=False),
        sa.Column('retrieval_query', sa.Text(), nullable=False),
        sa.Column('retriever', sa.Text(), nullable=False),
        sa.Column('score', sa.Numeric(6, 4), nullable=False),
        sa.Column('source_version', sa.Integer(), nullable=False),
        sa.Column('source_position', sa.Text(), nullable=False),
        sa.Column('knowledge_scope_id', sa.BigInteger(),
                  sa.ForeignKey('knowledge_scopes.scope_id'),
                  nullable=False, comment='Isolation: scope'),
        sa.Column('knowledge_scope_type', sa.Text(), nullable=False,
                  comment='project or public'),
        sa.Column('project_id', sa.BigInteger(), nullable=False,
                  comment='Isolation: project'),
        sa.Column('index_version', sa.Integer(), nullable=False,
                  comment='Isolation: derived index version'),
        sa.Column('referenced_by_agent', sa.Text(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),
        sa.Column('ttl_expires_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=True, comment='TTL expiry (blueprint sec 20)'),
        sa.PrimaryKeyConstraint('ledger_entry_id', name='pk_evidence_ledger_entry'),
        sa.CheckConstraint(
            "retriever IN ('dense','sparse','graph','fusion','rerank')",
            name='chk_ledger_retriever',
        ),
        sa.CheckConstraint(
            "referenced_by_agent IN ('query_planner','evidence_analyst','context_orchestrator')",
            name='chk_ledger_referenced_by_agent',
        ),
        sa.CheckConstraint('round_index >= 0', name='chk_ledger_round_index'),
        sa.CheckConstraint('sub_problem_id >= 1', name='chk_ledger_sub_problem_id'),
        sa.CheckConstraint('score >= 0 AND score <= 1', name='chk_ledger_score'),
        sa.CheckConstraint('source_version >= 1', name='chk_ledger_source_version'),
        comment='Append-only evidence ledger (005)',
    )
    op.create_index(
        'idx_ledger_scope', 'evidence_ledger_entry',
        ['knowledge_scope_id', 'project_id', 'index_version', 'created_at'],
    )
    op.create_index(
        'idx_ledger_run', 'evidence_ledger_entry',
        ['run_id', 'round_index', 'sub_problem_id'],
    )
    op.create_index(
        'idx_ledger_request_evidence', 'evidence_ledger_entry',
        ['request_id', 'evidence_id'],
    )

    # 2. agent_judgment (evidence analyst structured judgment)
    op.create_table(
        'agent_judgment',
        sa.Column('judgment_id', sa.BigInteger(), nullable=False,
                  comment='Snowflake ID'),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('round_index', sa.Integer(), nullable=False),
        sa.Column('coverage_state', sa.Text(), nullable=False),
        sa.Column('conflict_type', sa.Text(), nullable=False),
        sa.Column('uncovered_sub_problem_ids', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('needs_supplementary', sa.Boolean(), nullable=False),
        sa.Column('gap_descriptions', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('model_and_version', sa.Text(), nullable=False),
        sa.Column('schema_valid', sa.Boolean(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('judgment_id', name='pk_agent_judgment'),
        sa.CheckConstraint(
            "coverage_state IN ('covered','partial','uncovered')",
            name='chk_judgment_coverage_state',
        ),
        sa.CheckConstraint(
            "conflict_type IN ('none','version_conflict','source_conflict','domain_conflict')",
            name='chk_judgment_conflict_type',
        ),
        sa.CheckConstraint('round_index >= 0', name='chk_judgment_round_index'),
        comment='Evidence analyst judgments (005)',
    )
    op.create_index('idx_judgment_run', 'agent_judgment', ['run_id', 'round_index']),

    # 3. context_selection_list (append-only selection list)
    op.create_table(
        'context_selection_list',
        sa.Column('context_result_id', sa.Text(), nullable=False,
                  comment='Context orchestration result identifier'),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('ledger_entry_id', sa.BigInteger(),
                  sa.ForeignKey('evidence_ledger_entry.ledger_entry_id'),
                  nullable=False, comment='Selected/truncated/deduped entry'),
        sa.Column('decision', sa.Text(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('context_result_id', 'ledger_entry_id',
                                 name='pk_context_selection_list'),
        sa.CheckConstraint(
            "decision IN ('selected','truncated','deduped')",
            name='chk_selection_decision',
        ),
        comment='Append-only context selection list (005)',
    )
    op.create_index('idx_selection_run', 'context_selection_list', ['run_id']),

    # 4. agentic_retrieval_run (run record + state envelope)
    op.create_table(
        'agentic_retrieval_run',
        sa.Column('run_id', sa.BigInteger(), nullable=False,
                  comment='Snowflake ID'),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('project_scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('knowledge_scope_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('task_context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('run_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('completion_status', sa.Text(), nullable=False),
        sa.Column('max_rounds', sa.Integer(), nullable=False, server_default=sa.text('2')),
        sa.Column('rounds_completed', sa.Integer(), nullable=False),
        sa.Column('guardrail_state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sub_path_timings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('agent_outputs_ref', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('ledger_ref', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('total_cost', sa.Numeric(10, 4), nullable=True,
                  comment='LLM cost (SC-007)'),
        sa.Column('schema_valid_all', sa.Boolean(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),
        sa.Column('ttl_expires_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False,
                  server_default=sa.text("NOW() + INTERVAL '7 days'"),
                  comment='TTL expiry; cleanup deletes past rows'),
        sa.PrimaryKeyConstraint('run_id', name='pk_agentic_retrieval_run'),
        sa.CheckConstraint(
            "completion_status IN ('complete','partial','no_evidence','failed')",
            name='chk_agentic_run_completion_status',
        ),
        sa.CheckConstraint('max_rounds >= 1 AND max_rounds <= 3', name='chk_agentic_run_max_rounds'),
        sa.CheckConstraint('rounds_completed >= 0', name='chk_agentic_run_rounds_completed'),
        comment='Agent orchestration retrieval run (005)',
    )
    op.create_index('idx_run_request', 'agentic_retrieval_run', ['request_id']),
    op.create_index('idx_run_scope', 'agentic_retrieval_run', ['knowledge_scope_ids', 'created_at']),


def downgrade() -> None:
    op.drop_index('idx_run_scope', table_name='agentic_retrieval_run')
    op.drop_index('idx_run_request', table_name='agentic_retrieval_run')
    op.drop_table('agentic_retrieval_run')
    op.drop_index('idx_selection_run', table_name='context_selection_list')
    op.drop_table('context_selection_list')
    op.drop_index('idx_judgment_run', table_name='agent_judgment')
    op.drop_table('agent_judgment')
    op.drop_index('idx_ledger_request_evidence', table_name='evidence_ledger_entry')
    op.drop_index('idx_ledger_run', table_name='evidence_ledger_entry')
    op.drop_index('idx_ledger_scope', table_name='evidence_ledger_entry')
    op.drop_table('evidence_ledger_entry')
