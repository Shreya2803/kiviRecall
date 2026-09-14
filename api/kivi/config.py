from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    postgres_user: str
    postgres_password: str
    postgres_db: str

    sarvam_api_key: str | None = None
    sarvam_base_url: str = "https://api.sarvam.ai"

    extraction_model: str = "sarvam-105b"
    answer_model: str = "sarvam-105b"

    # Gemini-compatible fallback, used when SARVAM_API_KEY is absent so a reviewer
    # without a Sarvam key can still run everything. Currently Gemini's OpenAI
    # compatibility layer.
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_extraction_model: str = "gemini-3.5-flash-lite"
    gemini_answer_model: str = "gemini-3.5-flash"

    embedding_model: str = "BAAI/bge-m3"
    use_local_embeddings: bool = True

    # Retrieval (Phase 5). Thresholds live here, not hardcoded in sufficiency.py,
    # so a reviewer can see and tune the abstention boundary in one place.
    rrf_k: int = 60
    candidate_limit_per_generator: int = 30
    fused_top_n: int = 10
    sufficiency_min_fused_score: float = 0.02
    sufficiency_min_memory_confidence: float = 0.5


# Instantiated at import time so a missing required variable (DATABASE_URL,
# POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB) raises pydantic's
# ValidationError immediately on startup instead of failing later on first use.
settings = Settings()  # type: ignore[call-arg]


class ModelPricing(BaseModel):
    input_per_million: float  # USD per 1,000,000 input tokens
    output_per_million: float  # USD per 1,000,000 output tokens


# Sarvam publishes pricing in INR (docs.sarvam.ai/api-reference-docs/pricing).
# This is a fixed approximate conversion, not a live FX rate — revisit if it
# drifts far from reality; it only needs to be roughly right for a cost report,
# not exact to the cent.
USD_PER_INR = 1 / 83

MODEL_PRICING: dict[str, ModelPricing] = {
    # ₹29.28 / ₹73.2 per 1M tokens (in/out), fetched from Sarvam's pricing page.
    "sarvam-105b": ModelPricing(
        input_per_million=29.28 * USD_PER_INR,
        output_per_million=73.2 * USD_PER_INR,
    ),
    "sarvam-105b-conversations": ModelPricing(
        input_per_million=29.28 * USD_PER_INR,
        output_per_million=73.2 * USD_PER_INR,
    ),
    # Gemini pricing from ai.google.dev/gemini-api/docs/pricing, USD already.
    "gemini-3.5-flash-lite": ModelPricing(input_per_million=0.30, output_per_million=2.50),
    "gemini-3.5-flash": ModelPricing(input_per_million=1.50, output_per_million=9.00),
}
