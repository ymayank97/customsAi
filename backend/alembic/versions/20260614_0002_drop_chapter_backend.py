"""Drop chapter_backend from audit_jobs and training_feedback.

Revision ID: 20260614_0002
Revises: 20260203_0001
Create Date: 2026-06-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260614_0002"
down_revision: Union[str, None] = "20260203_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("audit_jobs", "chapter_backend")
    op.drop_column("training_feedback", "chapter_backend")


def downgrade() -> None:
    op.add_column("audit_jobs", sa.Column("chapter_backend", sa.String(length=32), nullable=True))
    op.add_column("training_feedback", sa.Column("chapter_backend", sa.String(length=16), nullable=True))
