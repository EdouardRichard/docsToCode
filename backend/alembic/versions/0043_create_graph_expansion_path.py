"""create_graph_expansion_path

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-02 03:00:00.000000

004 Graph RAG: creates the graph_expansion_path retrieval-run sub-table
recording the hop sequence from a start chunk to each graph-recalled evidence.

data-model §4 / DM-1: per (request_id, evidence_id) row with edge_path JSONB,
hop_count CHECK [1,3], structure_weight, graph_rank, and chunk_id ↔ evidence_id
bridge to the runtime trace ledger.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0043'
down_revision: Union[str, Sequence[str], None] = '0042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'graph_expansion_path',
        sa.Column('request_id', sa.BigInteger(),
                  sa.ForeignKey('retrieval_runs.run_id'),
                  nullable=False, comment='Owning retrieval request'),
        sa.Column('evidence_id', sa.BigInteger(),
                  sa.ForeignKey('chunks.chunk_id'),
                  nullable=False, comment='Graph-recalled evidence (chunk_id)'),
        sa.Column('chunk_id', sa.BigInteger(),
                  sa.ForeignKey('chunks.chunk_id'),
                  nullable=False, comment='Candidate chunk (DM-1 bridge to trace)'),
        sa.Column('start_chunk_id', sa.BigInteger(), nullable=False,
                  comment='Expansion start chunk'),
        sa.Column('edge_path', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, comment='Hop sequence: [{hop,edge_id,relation_type,direction,is_hard}]'),
        sa.Column('hop_count', sa.Integer(), nullable=False,
                  comment='Actual hop count within guardrail [1,3]'),
        sa.Column('structure_weight', sa.Numeric(precision=6, scale=4),
                  nullable=False, comment='Cumulative structure weight'),
        sa.Column('graph_rank', sa.Integer(), nullable=False,
                  comment='Graph candidate internal rank for RRF 3rd input'),
        sa.PrimaryKeyConstraint('request_id', 'evidence_id', name='pk_graph_expansion_path'),
        sa.CheckConstraint('hop_count >= 1 AND hop_count <= 3',
                           name='chk_graph_expansion_path_hops'),
        comment='Graph expansion paths per retrieval run (004)',
    )

    op.create_index(
        'idx_graph_expansion_path_request',
        'graph_expansion_path',
        ['request_id'],
    )
    op.create_index(
        'idx_graph_expansion_path_chunk',
        'graph_expansion_path',
        ['chunk_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_graph_expansion_path_chunk', table_name='graph_expansion_path')
    op.drop_index('idx_graph_expansion_path_request', table_name='graph_expansion_path')
    op.drop_table('graph_expansion_path')
