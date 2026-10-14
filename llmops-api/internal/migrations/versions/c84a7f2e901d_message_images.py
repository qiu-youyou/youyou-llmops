"""Persist chat image URLs.

Revision ID: c84a7f2e901d
Revises: b1263027e476
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c84a7f2e901d"
down_revision = "b1263027e476"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "message",
        sa.Column(
            "image_urls", postgresql.JSONB(),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("message", "image_urls")
