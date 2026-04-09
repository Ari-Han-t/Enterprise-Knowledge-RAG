from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Research Paper Analyzer"
    app_env: str = "development"
    jwt_secret: str = "change-this-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    database_url: str = "sqlite:///./data/app.db"
    upload_dir: str = "./data/uploads"
    redis_url: str | None = None

    llm_provider: str = "groq"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"

    dense_vector_size: int = 768

    max_file_size_bytes: int = 10 * 1024 * 1024
    max_uploads_per_day: int = 5
    max_queries_per_minute: int = 10
    max_input_tokens: int = 1200
    max_generation_tokens: int = 500
    max_queries_per_day: int =25
    top_k_dense: int = 8
    top_k_keyword: int = 8
    top_k_final: int = 6
    query_cache_ttl_seconds: int = 1800

    frontend_origins: str = Field(default="http://localhost:3000,http://localhost:5173")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
