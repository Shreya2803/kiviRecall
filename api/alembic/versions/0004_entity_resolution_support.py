"""entity resolution support: confidence, review flag, phonetic key

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")

    op.add_column(
        "entity",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "entity",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="false"),
    )

    # dmetaphone (from fuzzystrmatch) is the phonetic key for pass 3 — combined
    # with the pg_trgm GIN index already on `normalized` for similarity, and this
    # for "sounds like" matching independent of spelling.
    op.add_column(
        "entity_alias",
        sa.Column(
            "phonetic_key",
            sa.Text(),
            sa.Computed("dmetaphone(normalized)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index("ix_entity_alias_phonetic_key", "entity_alias", ["phonetic_key"])


def downgrade() -> None:
    op.drop_index("ix_entity_alias_phonetic_key", table_name="entity_alias")
    op.drop_column("entity_alias", "phonetic_key")
    op.drop_column("entity", "needs_review")
    op.drop_column("entity", "confidence")
    op.execute("DROP EXTENSION IF EXISTS fuzzystrmatch")
