"""memory.occurrence_count for reinforcement + retrieval's confidence boost

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memory",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("memory", "occurrence_count")
