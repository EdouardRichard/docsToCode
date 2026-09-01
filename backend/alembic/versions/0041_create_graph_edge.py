"""create_graph_edge

Revision ID: 0041
Revises: b3c4d5e6f7a8
Create Date: 2026-09-02 01:00:00.000000

004 Graph RAG: creates the graph_edge table for deterministic hard relations
(calls, called_by, fk_references, fk_referenced_by, other_hard).

data-model §2: graph_edge carries the isolation triple
(knowledge_scope_id, project_id, index_version), source/target chunk FKs,
relation_type CHECK (hard enum only — inferred lives in soft_relation),
direction CHECK (out|in), is_hard=true, parse_evidence JSONB, and version.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0041'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'graph_edge',
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
                  nullable=False, comment='Caller / FK-referencing node'),
        sa.Column('target_chunk_id', sa.BigInteger(),
                  sa.ForeignKey('chunks.chunk_id'),
                  nullable=False, comment='Callee / FK-referenced node'),
        sa.Column('relation_type', sa.Text(), nullable=False,
                  comment='Hard-relation enum (inferred forbidden in this table)'),
        sa.Column('direction', sa.Text(), nullable=False,
                  comment='Edge direction: out or in'),
        sa.Column('is_hard', sa.Boolean(), nullable=False,
                  server_default=sa.text('true'),
                  comment='Hard relation flag (always true in this table)'),
        sa.Column('version', sa.Integer(), nullable=False,
                  comment='Knowledge source version number'),
        sa.Column('parse_evidence', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False,
                  comment='Deterministic parse evidence (AST/DDL locator)'),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('edge_id', name='pk_graph_edge'),
        sa.CheckConstraint(
            "relation_type IN ('calls','called_by','fk_references',"
            "'fk_referenced_by','other_hard')",
            name='chk_graph_edge_relation_type',
        ),
        sa.CheckConstraint("direction IN ('out','in')", name='chk_graph_edge_direction'),
        sa.CheckConstraint("is_hard = true", name='chk_graph_edge_is_hard'),
        comment='Graph hard relations (004)',
    )

    # Forward expansion index (recursive CTE primary)
    op.create_index(
        'idx_graph_edge_source',
        'graph_edge',
        ['knowledge_scope_id', 'project_id', 'index_version',
         'source_chunk_id', 'relation_type', 'direction'],
    )
    # Reverse expansion index (called_by / fk_referenced_by)
    op.create_index(
        'idx_graph_edge_target',
        'graph_edge',
        ['knowledge_scope_id', 'project_id', 'index_version',
         'target_chunk_id', 'relation_type', 'direction'],
    )
    # Uniqueness: same version, same pair, same type/direction not duplicated
    op.create_index(
        'uniq_graph_edge',
        'graph_edge',
        ['knowledge_scope_id', 'index_version', 'source_chunk_id',
         'target_chunk_id', 'relation_type', 'direction', 'version'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uniq_graph_edge', table_name='graph_edge')
    op.drop_index('idx_graph_edge_target', table_name='graph_edge')
    op.drop_index('idx_graph_edge_source', table_name='graph_edge')
    op.drop_table('graph_edge')
