"""add_graph_ready

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-02 04:00:00.000000

004 Graph RAG: extends knowledge_versions with a graph_ready boolean column
(default false) for fast capability gating queries.

data-model §5: graph_ready=true implies dense_ready+lexical_ready (enforced
in capabilities JSONB + this denormalised flag). Declaring graph_ready before
graph relations are ready prevents publish (FR-013).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0044'
down_revision: Union[str, Sequence[str], None] = '0043'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'knowledge_versions',
        sa.Column('graph_ready', sa.Boolean(), nullable=False,
                  server_default=sa.text('false'),
                  comment='Graph relation capability ready (004, FR-013/FR-014)'),
    )


def downgrade() -> None:
    op.drop_column('knowledge_versions', 'graph_ready')
