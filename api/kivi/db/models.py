import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import Enum as SAEnum

from kivi.db.base import Base
from kivi.db.enums import (
    AliasMatchMethod,
    EntityType,
    ExtractionOutcome,
    MemoryStatus,
    MemoryType,
    SufficiencyVerdict,
    UserActionType,
)


class Dictation(Base):
    """Immutable. No update/delete path exists anywhere — enforced again at the DB
    level by a trigger in the 0002 migration, so a bug in application code can't
    violate it either."""

    __tablename__ = "dictation"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    spoken_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_asr: Mapped[str] = mapped_column(Text, nullable=False)
    formatted_output: Mapped[str] = mapped_column(Text, nullable=False)
    formatted_output_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', formatted_output)", persisted=True),
        nullable=False,
    )
    app_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    window_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    style_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    imported_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Entity(Base):
    __tablename__ = "entity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[EntityType] = mapped_column(SAEnum(EntityType, name="entity_type"), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    aliases: Mapped[list["EntityAlias"]] = relationship(back_populates="entity")


class EntityAlias(Base):
    __tablename__ = "entity_alias"
    __table_args__ = (
        UniqueConstraint("entity_id", "normalized", name="uq_entity_alias_entity_normalized"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entity.id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized: Mapped[str] = mapped_column(Text, nullable=False)
    match_method: Mapped[AliasMatchMethod] = mapped_column(
        SAEnum(AliasMatchMethod, name="alias_match_method"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped["Entity"] = relationship(back_populates="aliases")


class ExtractionRun(Base):
    """Written for every dictation processed, including the ~70% that produce
    nothing — `reason` is always populated in plain language."""

    __tablename__ = "extraction_run"
    __table_args__ = (Index("ix_extraction_run_dictation_id", "dictation_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dictation_id: Mapped[str] = mapped_column(String, ForeignKey("dictation.id"), nullable=False)
    outcome: Mapped[ExtractionOutcome] = mapped_column(
        SAEnum(ExtractionOutcome, name="extraction_outcome"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    memories_created_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Category counts only (e.g. {"health": 1}) — never the rejected claim text.
    # Logging the content would recreate the store the policy forbids.
    policy_rejections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Memory(Base):
    __tablename__ = "memory"
    __table_args__ = (
        CheckConstraint(
            "(status = 'superseded') = (superseded_by_id IS NOT NULL)",
            name="ck_memory_superseded_consistency",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_memory_valid_range",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    memory_type: Mapped[MemoryType] = mapped_column(SAEnum(MemoryType, name="memory_type"), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', claim)", persisted=True), nullable=False
    )
    # Hash of the normalised claim. Keys the partial unique index below, which is
    # what makes forget-tombstones survive re-import: a forgotten claim_hash can't
    # be re-inserted as active because the index still holds it.
    claim_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    status: Mapped[MemoryStatus] = mapped_column(
        SAEnum(MemoryStatus, name="memory_status"),
        nullable=False,
        server_default=MemoryStatus.ACTIVE.value,
    )
    valid_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("memory.id"), nullable=True)
    extraction_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("extraction_run.id"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MemorySource(Base):
    """Provenance join. Every memory must have >=1 row here — enforced by a
    deferred constraint trigger in the 0002 migration, not just documented."""

    __tablename__ = "memory_source"

    memory_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("memory.id"), primary_key=True)
    dictation_id: Mapped[str] = mapped_column(String, ForeignKey("dictation.id"), primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MemoryEntity(Base):
    """The entity join distributed facts are recovered through — entity_id leads
    the primary key because 'find every memory about entity X' is the hot path."""

    __tablename__ = "memory_entity"
    __table_args__ = (Index("ix_memory_entity_memory_id", "memory_id"),)

    entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("entity.id"), primary_key=True)
    memory_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("memory.id"), primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QueryTrace(Base):
    """Written for every Hey Kivi turn: filters, every candidate with its score,
    what was selected, the sufficiency verdict, and cost."""

    __tablename__ = "query_trace"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidates: Mapped[list] = mapped_column(JSONB, nullable=False)
    selected_memory_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    sufficiency_verdict: Mapped[SufficiencyVerdict] = mapped_column(
        SAEnum(SufficiencyVerdict, name="sufficiency_verdict"), nullable=False
    )
    sufficiency_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserAction(Base):
    """Explicit human commands via Hey Kivi (forget / correct), kept distinct from
    extraction_run so an answer can say whether a change was automatic or asked for."""

    __tablename__ = "user_action"
    __table_args__ = (
        CheckConstraint(
            "(action = 'correct') = (resulting_memory_id IS NOT NULL)",
            name="ck_user_action_correct_consistency",
        ),
        Index("ix_user_action_memory_id", "memory_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    memory_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("memory.id"), nullable=False)
    action: Mapped[UserActionType] = mapped_column(
        SAEnum(UserActionType, name="user_action_type"), nullable=False
    )
    resulting_memory_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("memory.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
