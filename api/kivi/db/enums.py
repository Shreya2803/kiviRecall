import enum


class MemoryType(str, enum.Enum):
    """The six things CLAUDE.md §2 says Kivi remembers. No TRAIT value — deliberate."""

    PROJECT_STATE = "project_state"
    ROLE = "role"
    DECISION = "decision"
    COMMITMENT = "commitment"
    TERMINOLOGY = "terminology"
    PREFERENCE = "preference"


class MemoryStatus(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"
    REJECTED = "rejected"


class EntityType(str, enum.Enum):
    PERSON = "person"
    PROJECT = "project"
    VENDOR = "vendor"
    TERM = "term"


class AliasMatchMethod(str, enum.Enum):
    """Which of the four resolution passes in CLAUDE.md §7 produced this alias."""

    EXACT = "exact"
    TRANSLITERATION = "transliteration"
    PHONETIC_TRIGRAM = "phonetic_trigram"
    MODEL_ASSISTED = "model_assisted"


class ExtractionOutcome(str, enum.Enum):
    MEMORIES_EXTRACTED = "memories_extracted"
    NO_MEMORIES_FOUND = "no_memories_found"
    ERROR = "error"


class SufficiencyVerdict(str, enum.Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class UserActionType(str, enum.Enum):
    FORGET = "forget"
    CORRECT = "correct"
