"""Add nullable columns: current_ma, inference_accel, and analysis_accel.

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-02 00:00:00.000000

These columns were originally added as ad-hoc ALTER TABLE statements in the
application startup handler. This migration formalizes them so that Alembic
manages their lifecycle going forward.

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable columns current_ma, inference_accel, and analysis_accel."""
    op.add_column(
        "inference_records",
        sa.Column("current_ma", sa.Float(), nullable=True),
    )
    op.add_column(
        "sensors",
        sa.Column("inference_accel", sa.String(16), nullable=True),
    )
    op.add_column(
        "sensors",
        sa.Column("analysis_accel", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    """Remove the nullable columns added in this migration."""
    op.drop_column("sensors", "analysis_accel")
    op.drop_column("sensors", "inference_accel")
    op.drop_column("inference_records", "current_ma")
