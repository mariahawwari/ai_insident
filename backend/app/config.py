from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache

class Settings(BaseSettings):
    # === Application ===
    APP_NAME: str = "AI Incident Analysis Agent"
    ENV: str = Field("development", description="environment: development | staging | production")

    # === Database ===
    DATABASE_URL: str = Field(
        "postgresql://postgres:postgres@localhost:5432/ai_agent",
        description="PostgreSQL connection string"
    )
    DB_POOL_MIN: int = 1
    DB_POOL_MAX: int = 10

    # === Embeddings / RAG ===
    MODEL_NAME: str = Field(
        "sentence-transformers/all-mpnet-base-v2",
        description="Embedding model for pgvector + RAG"
    )
    EMBEDDING_DIM: int = 768
    MAX_TOP_K: int = 20

    # === Knowledge Base ===
    RUNBOOK_DIR: str = "backend/app/knowledge/runbooks"

    # === Logging ===
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
