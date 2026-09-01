"""graph_enhanced_retrieval_mode

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-08 18:20:00.000000

004 Graph RAG: extends the retrieval_runs.retrieval_mode CHECK constraint
with 'graph_enhanced' (the configurable graph-enhanced retrieval path,
FR-006/FR-024) and requires subpath timings for it as well, mirroring the
hybrid-mode traceability rule (data-model sec 3.3, FR-026).
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0045'
down_revision: Union[str, Sequence[str], None] = '0044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('chk_retrieval_mode', 'retrieval_runs', type_='check')
    op.create_check_constraint(
        'chk_retrieval_mode',
        'retrieval_runs',
        "retrieval_mode IN ('dense', 'hybrid', 'graph_enhanced')",
    )
    op.drop_constraint('chk_hybrid_timings', 'retrieval_runs', type_='check')
    op.create_check_constraint(
        'chk_hybrid_timings',
        'retrieval_runs',
        "retrieval_mode NOT IN ('hybrid', 'graph_enhanced') OR "
        "(subpath_timings IS NOT NULL AND subpath_timings::text <> 'null')",
    )


def downgrade() -> None:
    op.drop_constraint('chk_hybrid_timings', 'retrieval_runs', type_='check')
    op.create_check_constraint(
        'chk_hybrid_timings',
        'retrieval_runs',
        "retrieval_mode <> 'hybrid' OR "
        "(subpath_timings IS NOT NULL AND subpath_timings::text <> 'null')",
    )
    op.drop_constraint('chk_retrieval_mode', 'retrieval_runs', type_='check')
    op.create_check_constraint(
        'chk_retrieval_mode',
        'retrieval_runs',
        "retrieval_mode IN ('dense', 'hybrid')",
    )
