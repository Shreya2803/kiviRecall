"""core schema: dictation, entity, memory, and the audit trail tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- dictation ---------------------------------------------------------
    op.create_table(
        "dictation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_asr", sa.Text(), nullable=False),
        sa.Column("formatted_output", sa.Text(), nullable=False),
        sa.Column(
            "formatted_output_tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', formatted_output)", persisted=True),
            nullable=False,
        ),
        sa.Column("app_context", sa.Text(), nullable=True),
        sa.Column("window_title", sa.Text(), nullable=True),
        sa.Column("language", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("style_id", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_unique_constraint("uq_dictation_content_hash", "dictation", ["content_hash"])
    op.create_index(
        "ix_dictation_formatted_output_tsv",
        "dictation",
        ["formatted_output_tsv"],
        postgresql_using="gin",
    )

    # Immutability is a hard rule 
    # . Enforcing it only in application code means one missed
    # code path breaks the guarantee silently, so it is enforced again here.
    op.execute(
        """
        CREATE FUNCTION forbid_dictation_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'dictation rows are immutable (id=%), % is not permitted', OLD.id, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dictation_immutable
        BEFORE UPDATE OR DELETE ON dictation
        FOR EACH ROW EXECUTE FUNCTION forbid_dictation_mutation();
        """
    )

    # --- entity / entity_alias ----------------------------------------------
    op.create_table(
        "entity",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "entity_type",
            sa.Enum("person", "project", "vendor", "term", name="entity_type"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    op.create_table(
        "entity_alias",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("entity_id", sa.BigInteger(), sa.ForeignKey("entity.id"), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column(
            "match_method",
            sa.Enum(
                "exact", "transliteration", "phonetic_trigram", "model_assisted", name="alias_match_method"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("entity_id", "normalized", name="uq_entity_alias_entity_normalized"),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # already enabled in 0001; defensive
    op.create_index(
        "ix_entity_alias_normalized_trgm",
        "entity_alias",
        ["normalized"],
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )

    # --- extraction_run -------------------------------------------------------
    op.create_table(
        "extraction_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dictation_id", sa.String(), sa.ForeignKey("dictation.id"), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum("memories_extracted", "no_memories_found", "error", name="extraction_outcome"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("memories_created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_rejections", JSONB(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_extraction_run_dictation_id", "extraction_run", ["dictation_id"])

    # --- memory ----------------------------------------------------------------
    op.create_table(
        "memory",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "memory_type",
            sa.Enum(
                "project_state",
                "role",
                "decision",
                "commitment",
                "terminology",
                "preference",
                name="memory_type",
            ),
            nullable=False,
        ),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column(
            "claim_tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', claim)", persisted=True),
            nullable=False,
        ),
        sa.Column("claim_hash", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "superseded", "forgotten", "rejected", name="memory_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "extraction_run_id", sa.BigInteger(), sa.ForeignKey("extraction_run.id"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["memory.id"], name="fk_memory_superseded_by"),
        sa.CheckConstraint(
            "(status = 'superseded') = (superseded_by_id IS NOT NULL)",
            name="ck_memory_superseded_consistency",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_memory_valid_range"
        ),
    )
    op.create_index("ix_memory_claim_tsv", "memory", ["claim_tsv"], postgresql_using="gin")
    op.create_index("ix_memory_status", "memory", ["status"])
    op.create_index("ix_memory_valid_from", "memory", ["valid_from"])
    op.create_index(
        "ix_memory_embedding_hnsw",
        "memory",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # The tombstone mechanism: a forgotten claim_hash stays reserved because this
    # index still enforces uniqueness across BOTH active and forgotten rows, so
    # re-import cannot silently resurrect a forgotten claim as a new active one.
    op.create_index(
        "uq_memory_claim_hash_active_forgotten",
        "memory",
        ["claim_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'forgotten')"),
    )

    # --- memory_source (provenance) --------------------------------------------
    op.create_table(
        "memory_source",
        sa.Column("memory_id", sa.BigInteger(), sa.ForeignKey("memory.id"), primary_key=True),
        sa.Column("dictation_id", sa.String(), sa.ForeignKey("dictation.id"), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    # Postgres has no declarative "must have >=1 child row" constraint. A deferred
    # constraint trigger is the standard way to enforce it: it fires after INSERT
    # on memory but the check runs at COMMIT time, so the application can insert
    # memory and its memory_source row(s) in either order within one transaction.
    op.execute(
        """
        CREATE FUNCTION check_memory_has_source() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM memory_source WHERE memory_id = NEW.id) THEN
                RAISE EXCEPTION 'memory % has no memory_source row (provenance invariant violated)', NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_memory_has_source
        AFTER INSERT ON memory
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_memory_has_source();
        """
    )

    # --- memory_entity (the entity join) ----------------------------------------
    op.create_table(
        "memory_entity",
        sa.Column("entity_id", sa.BigInteger(), sa.ForeignKey("entity.id"), primary_key=True),
        sa.Column("memory_id", sa.BigInteger(), sa.ForeignKey("memory.id"), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_memory_entity_memory_id", "memory_entity", ["memory_id"])

    # --- query_trace -------------------------------------------------------------
    op.create_table(
        "query_trace",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("filters", JSONB(), nullable=True),
        sa.Column("candidates", JSONB(), nullable=False),
        sa.Column("selected_memory_ids", sa.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column(
            "sufficiency_verdict",
            sa.Enum("sufficient", "insufficient", name="sufficiency_verdict"),
            nullable=False,
        ),
        sa.Column("sufficiency_reason", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    # --- user_action ---------------------------------------------------------------
    op.create_table(
        "user_action",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("memory_id", sa.BigInteger(), sa.ForeignKey("memory.id"), nullable=False),
        sa.Column("action", sa.Enum("forget", "correct", name="user_action_type"), nullable=False),
        sa.Column(
            "resulting_memory_id", sa.BigInteger(), sa.ForeignKey("memory.id"), nullable=True
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "(action = 'correct') = (resulting_memory_id IS NOT NULL)",
            name="ck_user_action_correct_consistency",
        ),
    )
    op.create_index("ix_user_action_memory_id", "user_action", ["memory_id"])


def downgrade() -> None:
    op.drop_table("user_action")
    op.drop_table("query_trace")
    op.drop_table("memory_entity")

    op.drop_table("memory_source")
    op.execute("DROP TRIGGER IF EXISTS trg_memory_has_source ON memory")
    op.execute("DROP FUNCTION IF EXISTS check_memory_has_source()")

    op.drop_index("uq_memory_claim_hash_active_forgotten", table_name="memory")
    op.drop_index("ix_memory_embedding_hnsw", table_name="memory")
    op.drop_index("ix_memory_valid_from", table_name="memory")
    op.drop_index("ix_memory_status", table_name="memory")
    op.drop_index("ix_memory_claim_tsv", table_name="memory")
    op.drop_table("memory")

    op.drop_index("ix_extraction_run_dictation_id", table_name="extraction_run")
    op.drop_table("extraction_run")

    op.drop_index("ix_entity_alias_normalized_trgm", table_name="entity_alias")
    op.drop_table("entity_alias")
    op.drop_table("entity")

    op.execute("DROP TRIGGER IF EXISTS trg_dictation_immutable ON dictation")
    op.execute("DROP FUNCTION IF EXISTS forbid_dictation_mutation()")
    op.drop_index("ix_dictation_formatted_output_tsv", table_name="dictation")
    op.drop_table("dictation")

    for enum_name in (
        "user_action_type",
        "sufficiency_verdict",
        "memory_status",
        "memory_type",
        "extraction_outcome",
        "alias_match_method",
        "entity_type",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
