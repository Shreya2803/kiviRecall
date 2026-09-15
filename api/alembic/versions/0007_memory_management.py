"""memory management: origin, stored confidence, last_confirmed_at,
user_action_type.confirm

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_action_type ADD VALUE IF NOT EXISTS 'confirm'")

    # 'extracted' (the default, written by the pipeline) vs 'user' (written by
    # a Hey Kivi correction) — lets the UI and retrieval tell the two apart.
    memory_origin = sa.Enum("extracted", "user", name="memory_origin")
    memory_origin.create(op.get_bind())
    op.add_column(
        "memory", sa.Column("origin", memory_origin, nullable=False, server_default="extracted")
    )
    # Previously derived live in fusion by averaging linked entities' confidence
    # on every query. Phase 6 needs a memory to carry its OWN confidence so a
    # user correction can set it to 1.0 directly — stored once at creation
    # (from the same entity-average) instead of recomputed every retrieval.
    op.add_column("memory", sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"))
    # "first learned" is valid_from/created_at, already on the row. This is the
    # other half "What Kivi knows" needs: when was this last reinforced or
    # explicitly confirmed by the person.
    op.add_column("memory", sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("memory", "last_confirmed_at")
    op.drop_column("memory", "confidence")
    op.drop_column("memory", "origin")
    sa.Enum(name="memory_origin").drop(op.get_bind())
    # Postgres cannot drop a single enum value — 'confirm' stays, harmlessly unused.
