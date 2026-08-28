"""add_hybrid_retrieval_fields

Revision ID: e8a2c1b7d4f3
Revises: 643470736319
Create Date: 2026-08-27 02:00:00.000000

002 Hybrid Retrieval Precision: extends retrieval_runs table with
retrieval_mode, subpath_timings, and evidence_ref_ids columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e8a2c1b7d4f3'
down_revision: Union[str, Sequence[str], None] = '643470736319'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add hybrid retrieval fields to retrieval_runs table (data-model §6.1)."""
    # Add columns with defaults for backward compatibility with 001 records
    op.add_column(
        'retrieval_runs',
        sa.Column(
            'retrieval_mode',
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'dense'"),
            comment="'dense' (001) or 'hybrid' (002 Dense+Sparse+RRF+Rerank)",
        ),
    )
    op.add_column(
        'retrieval_runs',
        sa.Column(
            'subpath_timings',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Hybrid retrieval sub-path timings (dense/sparse/fusion/rerank/total ms)',
        ),
    )
    op.add_column(
        'retrieval_runs',
        sa.Column(
            'evidence_ref_ids',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment='Returned evidence IDs for problem tracing',
        ),
    )

    # CHECK constraint: retrieval_mode must be 'dense' or 'hybrid'
    op.create_check_constraint(
        'chk_retrieval_mode',
        'retrieval_runs',
        "retrieval_mode IN ('dense', 'hybrid')",
    )

    # CHECK constraint: hybrid mode must have subpath_timings
    op.create_check_constraint(
        'chk_hybrid_timings',
        'retrieval_runs',
        "retrieval_mode <> 'hybrid' OR "
        "(subpath_timings IS NOT NULL AND subpath_timings::text <> 'null')",
    )

    # Index for querying by mode + time range
    op.create_index(
        'idx_rr_mode_created',
        'retrieval_runs',
        ['retrieval_mode', 'created_at'],
    )


def downgrade() -> None:
    """Remove hybrid retrieval fields from retrieval_runs table."""
    op.drop_index('idx_rr_mode_created', table_name='retrieval_runs')
    op.drop_constraint('chk_hybrid_timings', 'retrieval_runs', type_='check')
    op.drop_constraint('chk_retrieval_mode', 'retrieval_runs', type_='check')
    op.drop_column('retrieval_runs', 'evidence_ref_ids')
    op.drop_column('retrieval_runs', 'subpath_timings')
    op.drop_column('retrieval_runs', 'retrieval_mode')
