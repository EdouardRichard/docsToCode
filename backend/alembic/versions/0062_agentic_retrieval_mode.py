"""agentic_retrieval_mode

Revision ID: 0062
Revises: 0061
Create Date: 2026-09-03 00:00:00.000000

006 Runtime Hardening: extends the retrieval_runs.retrieval_mode CHECK
constraint with 'agentic' so the agentic orchestration path records its run
activity in retrieval_runs (FR-016/FR-018 — runtime metrics cover all four
retrieval modes). The deterministic dense/hybrid/graph_enhanced values are
unchanged.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0062'
down_revision: Union[str, Sequence[str], None] = '0061'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('chk_retrieval_mode', 'retrieval_runs', type_='check')
    op.create_check_constraint(
        'chk_retrieval_mode',
        'retrieval_runs',
        "retrieval_mode IN ('dense', 'hybrid', 'graph_enhanced', 'agentic')",
    )


def downgrade() -> None:
    op.drop_constraint('chk_retrieval_mode', 'retrieval_runs', type_='check')
    op.create_check_constraint(
        'chk_retrieval_mode',
        'retrieval_runs',
        "retrieval_mode IN ('dense', 'hybrid', 'graph_enhanced')",
    )
