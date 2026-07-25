# src/core/config.py
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API Keys
    # They are optional at API startup. The IA worker validates its own key.
    groq_api_key: Optional[str] = None
    digisac_api_key: Optional[str] = None
    digisac_api_base_url: str = "https://inov.digisac.chat/api/v1"
    webhook_secret: Optional[str] = None

    # Database & Cache
    redis_url: str = "redis://localhost:6379"
    redis_max_connections: int = 10
    redis_db: int = 0
    database_url: Optional[str] = None
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    database_pool_timeout_seconds: float = 10.0

    # Application
    debug: bool = False
    log_level: str = "INFO"
    environment: str = "development"

    # Worker Settings
    ia_timeout_seconds: int = 60
    ticket_buffer_ttl_seconds: int = 60 * 60 * 24 * 30
    closed_ticket_ttl_seconds: int = 60 * 60 * 24 * 30
    ticket_closure_debounce_seconds: float = 2.0
    result_ttl_seconds: int = 86400

    # AI Settings
    model_name: str = "openai/gpt-oss-120b"
    temperature: float = 0.3
    max_tokens: int = 3000
    prompt_version: str = "v4"

    # Webhook
    webhook_url: str = "http://localhost:8000/webhook/digisac"
    webhook_timeout: int = 30
    max_retry_attempts: int = 3
    audio_download_timeout_seconds: int = 30
    audio_conversion_timeout_seconds: int = 60
    audio_transcription_timeout_seconds: int = 60
    audio_transcription_model: str = "whisper-large-v3-turbo"
    image_vision_model: str = "qwen/qwen3.6-27b"
    image_vision_max_completion_tokens: int = 5000
    image_max_bytes: int = 4 * 1024 * 1024
    image_download_timeout_seconds: int = 30
    image_extraction_timeout_seconds: int = 60
    content_extraction_wait_seconds: float = 30.0
    content_extraction_poll_seconds: float = 0.5
    digisac_directory_timeout_seconds: int = 15
    digisac_directory_max_retries: int = 3
    digisac_directory_sync_interval_seconds: int = 60 * 60 * 24
    digisac_directory_refresh_cooldown_seconds: int = 60 * 15

    # Monitoring
    prometheus_port: int = 9090
    sentry_dsn: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug(cls, value):
        if isinstance(value, str) and value.lower() in {"release", "production"}:
            return False
        return value


settings = Settings()
