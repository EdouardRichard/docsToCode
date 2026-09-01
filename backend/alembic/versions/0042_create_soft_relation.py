"""create_soft_relation

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-02 02:00:00.000000

004 Graph RAG: creates the soft_relation table for LLM-inferred relations
with five mandatory metadata fields and a four-state lifecycle
(inferred → active → superseded → retired).

data-model §3: soft_relation carries the isolation triple, relation_type
fixed to 'inferred', is_hard=false, five metadata fields (inference_source,
confidence, model_and_version, generated_at, supporting_evidence_ids),
lifecycle_state CHECK (4 states), and deterministic supersede traceability
(superseded_by / superseded_at).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0042'
down_revision: Union[str, Sequence[str], None] = '0041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'soft_relation',
        sa.Column('edge_id', sa.BigInteger(), nullable=False, comment='Snowflake ID'),
        sa.Column('knowledge_scope_id', sa.BigInteger(),
                  sa.ForeignKey('knowledge_scopes.scope_id'),
                  nullable=False, comment='Isolation: scope'),
        sa.Column('project_id', sa.BigInteger(), nullable=False,
                  comment='Isolation: project'),
        sa.Column('index_version', sa.Integer(), nullable=False,
                  comment='Isolation: derived index version'),
        sa.Column('source_chunk_id', sa.BigInteger(),
                  sa.ForeignKey('chunks.chunk_id'),
                  nullable=False, comment='Inferred relation source node'),
        sa.Column('target_chunk_id', sa.BigInteger(),
                  sa.ForeignKey('chunks.chunk_id'),
                  nullable=False, comment='Inferred relation target node'),
        sa.Column('relation_type', sa.Text(), nullable=False,
                  server_default=sa.text("'inferred'"),
                  comment='Fixed to inferred for soft relations'),
        sa.Column('direction', sa.Text(), nullable=False,
                  comment='Inferred direction: out or in'),
        sa.Column('is_hard', sa.Boolean(), nullable=False,
                  server_default=sa.text('false'),
                  comment='Soft relation flag (always false)'),
        sa.Column('version', sa.Integer(), nullable=False,
                  comment='Knowledge source version number'),
        # Five mandatory metadata
        sa.Column('inference_source', sa.Text(), nullable=False,
                  comment='Metadata 1: inference source'),
        sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=False,
                  comment='Metadata 2: confidence in [0,1]'),
        sa.Column('model_and_version', sa.Text(), nullable=False,
                  comment='Metadata 3: LLM model and version'),
        sa.Column('generated_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False, comment='Metadata 4: generation time'),
        sa.Column('supporting_evidence_ids', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, comment='Metadata 5: supporting evidence IDs'),
        # Four-state lifecycle
        sa.Column('lifecycle_state', sa.Text(), nullable=False,
                  server_default=sa.text("'inferred'"),
                  comment='inferred | active | superseded | retired'),
        sa.Column('superseded_by', sa.BigInteger(), nullable=True,
                  comment='Edge ID that superseded this relation (traceable)'),
        sa.Column('superseded_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=True, comment='When this relation was superseded'),
        sa.PrimaryKeyConstraint('edge_id', name='pk_soft_relation'),
        sa.CheckConstraint("relation_type = 'inferred'", name='chk_soft_relation_type'),
        sa.CheckConstraint("direction IN ('out','in')", name='chk_soft_relation_direction'),
        sa.CheckConstraint("is_hard = false", name='chk_soft_relation_is_hard'),
        sa.CheckConstraint(
            "lifecycle_state IN ('inferred','active','superseded','retired')",
            name='chk_soft_relation_lifecycle',
        ),
        comment='Graph soft relations (004, 4-state lifecycle)',
    )

    # Supersede judgement primary index
    op.create_index(
        'idx_soft_relation_pair',
        'soft_relation',
        ['knowledge_scope_id', 'index_version', 'source_chunk_id',
         'target_chunk_id', 'relation_type', 'lifecycle_state'],
    )
    # Active-only partial index for default-path low-weight supplement
    op.create_index(
        'idx_soft_relation_active',
        'soft_relation',
        ['knowledge_scope_id', 'project_id', 'index_version', 'lifecycle_state'],
        postgresql_where=sa.text("lifecycle_state = 'active'"),
    )


def downgrade() -> None:
    op.drop_index('idx_soft_relation_active', table_name='soft_relation')
    op.drop_index('idx_soft_relation_pair', table_name='soft_relation')
    op.drop_table('soft_relation')
