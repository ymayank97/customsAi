"""Initial schema: users, audit_jobs, training_feedback.

Revision ID: 20260203_0001
Revises:
Create Date: 2026-02-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260203_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("google_sub", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("picture", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_index("ix_users_created_at", "users", ["created_at"], unique=False)

    op.create_table(
        "audit_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("chapter_backend", sa.String(length=32), nullable=True),
        sa.Column("input_path", sa.Text(), nullable=True),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        sa.Column("rows_processed", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("completed_at", sa.Float(), nullable=True),
        sa.Column("eta_seconds", sa.Float(), nullable=True),
        sa.Column("progress_data", sa.JSON(), nullable=True),
        sa.Column("columns_detected", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_jobs_user_id", "audit_jobs", ["user_id"], unique=False)
    op.create_index("ix_audit_jobs_status", "audit_jobs", ["status"], unique=False)
    op.create_index("ix_audit_jobs_started_at", "audit_jobs", ["started_at"], unique=False)

    op.create_table(
        "training_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("row_idx", sa.Integer(), nullable=True),
        sa.Column("item_description", sa.Text(), nullable=True),
        sa.Column("declared_cth", sa.String(length=16), nullable=True),
        sa.Column("predicted_cth", sa.String(length=16), nullable=True),
        sa.Column("predicted_bcd", sa.Float(), nullable=True),
        sa.Column("corrected_cth", sa.String(length=16), nullable=True),
        sa.Column("corrected_bcd", sa.Float(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("user_label", sa.String(length=120), nullable=True),
        sa.Column("chapter_backend", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_feedback_user_id", "training_feedback", ["user_id"], unique=False)
    op.create_index("ix_training_feedback_job_id", "training_feedback", ["job_id"], unique=False)
    op.create_index("ix_training_feedback_row_idx", "training_feedback", ["row_idx"], unique=False)
    op.create_index("ix_training_feedback_created_at", "training_feedback", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_training_feedback_created_at", table_name="training_feedback")
    op.drop_index("ix_training_feedback_row_idx", table_name="training_feedback")
    op.drop_index("ix_training_feedback_job_id", table_name="training_feedback")
    op.drop_index("ix_training_feedback_user_id", table_name="training_feedback")
    op.drop_table("training_feedback")
    op.drop_index("ix_audit_jobs_started_at", table_name="audit_jobs")
    op.drop_index("ix_audit_jobs_status", table_name="audit_jobs")
    op.drop_index("ix_audit_jobs_user_id", table_name="audit_jobs")
    op.drop_table("audit_jobs")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_table("users")
