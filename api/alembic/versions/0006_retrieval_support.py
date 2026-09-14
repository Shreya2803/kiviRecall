"""retrieval support: sufficiency_verdict.partial, query_trace routing/citation
columns, memory.pinned

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New value only — not used within this same transaction, which is the one
    # thing Postgres forbids for ALTER TYPE ... ADD VALUE inside a transaction.
    op.execute("ALTER TYPE sufficiency_verdict ADD VALUE IF NOT EXISTS 'partial'")

    op.add_column("query_trace", sa.Column("route", sa.Text(), nullable=True))
    op.add_column(
        "query_trace", sa.Column("cited_memory_ids", sa.ARRAY(sa.BigInteger()), nullable=True)
    )
    op.add_column("query_trace", sa.Column("retrieval_latency_ms", sa.Integer(), nullable=True))
    op.add_column("query_trace", sa.Column("generation_latency_ms", sa.Integer(), nullable=True))

    # Nothing sets this yet (no pin action/tool exists) — the fusion stage's
    # "force pinned to top" step needs the column to exist and always reads
    # false today. Wiring an action that sets it is future work, not this phase.
    op.add_column(
        "memory", sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false")
    )


def downgrade() -> None:
    op.drop_column("memory", "pinned")
    op.drop_column("query_trace", "generation_latency_ms")
    op.drop_column("query_trace", "retrieval_latency_ms")
    op.drop_column("query_trace", "cited_memory_ids")
    op.drop_column("query_trace", "route")
    # Postgres cannot drop a single enum value — downgrading past this point
    # leaves 'partial' in the type, which is harmless (an unused enum label).
