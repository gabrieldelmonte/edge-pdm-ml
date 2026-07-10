"""Initial schema: users, sensors, and inference_records tables.

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the initial users, sensors, and inference_records tables."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "sensors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sensor_uid", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("bearing_type", sa.String(32), nullable=False),
        sa.Column("selected_model", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("inference_accel", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sensor_uid"),
    )
    op.create_index(op.f("ix_sensors_id"), "sensors", ["id"], unique=False)
    op.create_index(op.f("ix_sensors_sensor_uid"), "sensors", ["sensor_uid"], unique=True)
    op.create_index(op.f("ix_sensors_user_id"), "sensors", ["user_id"], unique=False)

    op.create_table(
        "inference_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(256), nullable=False),
        sa.Column("checksum", sa.String(256), nullable=False),
        sa.Column("selected_model", sa.String(64), nullable=False),
        sa.Column("selected_inference", sa.String(128), nullable=False),
        sa.Column("model_results_json", sa.Text(), nullable=False),
        sa.Column("analysis_json", sa.Text(), nullable=False),
        sa.Column("csv_data", sa.Text(), nullable=False),
        sa.Column("current_ma", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_inference_records_id"), "inference_records", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_inference_records_file_id"), "inference_records", ["file_id"], unique=False
    )
    op.create_index(
        op.f("ix_inference_records_sensor_id"),
        "inference_records",
        ["sensor_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop all tables created in this migration."""
    op.drop_index(op.f("ix_inference_records_sensor_id"), table_name="inference_records")
    op.drop_index(op.f("ix_inference_records_file_id"), table_name="inference_records")
    op.drop_index(op.f("ix_inference_records_id"), table_name="inference_records")
    op.drop_table("inference_records")

    op.drop_index(op.f("ix_sensors_user_id"), table_name="sensors")
    op.drop_index(op.f("ix_sensors_sensor_uid"), table_name="sensors")
    op.drop_index(op.f("ix_sensors_id"), table_name="sensors")
    op.drop_table("sensors")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
