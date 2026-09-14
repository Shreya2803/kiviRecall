"""views: entity_knowledge, memory_history

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW entity_knowledge AS
        SELECT
            e.id AS entity_id,
            e.canonical_name,
            e.entity_type,
            m.id AS memory_id,
            m.memory_type,
            m.claim,
            m.valid_from,
            m.valid_to,
            COUNT(ms.dictation_id) AS source_count
        FROM entity e
        JOIN memory_entity me ON me.entity_id = e.id
        JOIN memory m ON m.id = me.memory_id AND m.status = 'active'
        LEFT JOIN memory_source ms ON ms.memory_id = m.id
        GROUP BY e.id, e.canonical_name, e.entity_type, m.id, m.memory_type, m.claim,
                 m.valid_from, m.valid_to;
        """
    )

    op.execute(
        """
        CREATE VIEW memory_history AS
        SELECT
            old.id AS memory_id,
            old.claim AS old_claim,
            old.status,
            old.valid_from,
            old.valid_to,
            new.id AS superseded_by_id,
            new.claim AS new_claim
        FROM memory old
        LEFT JOIN memory new ON new.id = old.superseded_by_id
        WHERE old.superseded_by_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS memory_history")
    op.execute("DROP VIEW IF EXISTS entity_knowledge")
