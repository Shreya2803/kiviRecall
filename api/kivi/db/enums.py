import enum


class MemoryType(str, enum.Enum):
    

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
    CONFIRM = "confirm"


class MemoryOrigin(str, enum.Enum):
    EXTRACTED = "extracted"
    USER = "user"
