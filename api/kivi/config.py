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

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    embedding_model: str = "BAAI/bge-m3"
    use_local_embeddings: bool = True


# Instantiated at import time so a missing required variable (DATABASE_URL,
# POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB) raises pydantic's
# ValidationError immediately on startup instead of failing later on first use.
settings = Settings()  # type: ignore[call-arg]
