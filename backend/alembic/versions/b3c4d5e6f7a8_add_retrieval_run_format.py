"""add_retrieval_run_format

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-28 12:00:00.000000

003: adds format column to retrieval_runs for audit tracking (FR-027).
Nullable; records the format of the top-1 evidence hit.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'retrieval_runs',
        sa.Column(
            'format',
            sa.String(length=8),
            nullable=True,
            comment='Format of top-1 evidence hit (003, FR-027)',
        ),
    )
    op.create_check_constraint(
        'chk_retrieval_run_format',
        'retrieval_runs',
        "format IS NULL OR format IN ('markdown','java','openapi','ddl','go','python','word','pdf')",
    )

def downgrade():
    op.drop_constraint('chk_retrieval_run_format', 'retrieval_runs', type_='check')
    op.drop_column('retrieval_runs', 'format')