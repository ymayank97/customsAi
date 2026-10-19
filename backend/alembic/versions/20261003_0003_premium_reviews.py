from alembic import op
import sqlalchemy as sa

revision = "20261003_0003"
down_revision = "20260614_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("account_tier", sa.String(16), nullable=False, server_default="standard"))
    op.create_table(
        "training_labels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("audit_jobs.id"), nullable=False),
        sa.Column("row_idx", sa.Integer, nullable=False),
        sa.Column("item_description", sa.Text, nullable=False),
        sa.Column("declared_cth", sa.String(16)),
        sa.Column("predicted_cth", sa.String(16)),
        sa.Column("predicted_bcd", sa.Float),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("corrected_cth", sa.String(8)),
        sa.Column("corrected_bcd", sa.Float),
        sa.Column("reference", sa.Text, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=False),
        sa.Column("product_details", sa.Text),
        sa.Column("reviewer_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("user_id", "job_id", "row_idx", name="uq_training_label_row"),
    )
    for column in ("user_id", "job_id", "created_at", "updated_at"):
        op.create_index(f"ix_training_labels_{column}", "training_labels", [column])


def downgrade():
    op.drop_table("training_labels")
    op.drop_column("users", "account_tier")
