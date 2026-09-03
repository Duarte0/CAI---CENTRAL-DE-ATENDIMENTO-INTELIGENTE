# src/core/config.py
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API Keys
    # They are optional at API startup. The IA worker validates its own key.
    groq_api_key: Optional[str] = None
    digisac_api_key: Optional[str] = None
    digisac_api_base_url: str = "https://inov.digisac.chat/api/v1"
    acessorias_api_token: Optional[str] = None
    acessorias_api_base_url: str = "https://api.acessorias.com"
    webhook_secret: Optional[str] = None
    admin_api_token: Optional[str] = None
    admin_ui_password: Optional[str] = None
    admin_session_secret: Optional[str] = None

    # Database & Cache
    redis_url: str = "redis://localhost:6379"
    redis_max_connections: int = 10
    redis_db: int = 0
    database_url: Optional[str] = None
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    database_pool_timeout_seconds: float = 10.0
    database_statement_timeout_ms: int = 15_000
    database_lock_timeout_ms: int = 3_000
    database_idle_transaction_timeout_ms: int = 30_000
    app_timezone: str = "America/Sao_Paulo"

    # Application
    debug: bool = False
    log_level: str = "INFO"
    environment: str = "development"

    # Worker Settings
    ia_timeout_seconds: int = 60

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
    audio_retry_base_seconds: float = 2.0
    audio_retry_max_delay_seconds: float = 15 * 60
    audio_retry_provider_margin_seconds: float = 1.0
    image_vision_model: str = "qwen/qwen3.6-27b"
    image_vision_max_completion_tokens: int = 5000
    image_max_bytes: int = 4 * 1024 * 1024
    image_download_timeout_seconds: int = 30
    image_extraction_timeout_seconds: int = 60
    image_retry_base_seconds: float = 2.0
    image_retry_max_delay_seconds: float = 15 * 60
    image_retry_provider_margin_seconds: float = 1.0
    ia_retry_base_seconds: float = 2.0
    ia_retry_max_delay_seconds: float = 15 * 60
    ia_retry_provider_margin_seconds: float = 1.0
    content_extraction_wait_seconds: float = 30.0
    content_extraction_poll_seconds: float = 0.5
    content_recovery_lease_seconds: int = 300
    content_recovery_batch_size: int = 100
    content_reconcile_interval_seconds: float = 5.0
    digisac_directory_timeout_seconds: int = 15
    digisac_directory_max_retries: int = 3
    digisac_directory_sync_interval_seconds: int = 60 * 60 * 24
    digisac_directory_refresh_cooldown_seconds: int = 60 * 15
    acessorias_request_timeout_seconds: float = 15.0
    acessorias_max_attempts: int = 3
    acessorias_retry_base_seconds: float = 1.0
    acessorias_retry_max_delay_seconds: float = 60.0
    acessorias_retry_provider_margin_seconds: float = 1.0
    acessorias_rate_limit_per_minute: int = 100
    acessorias_page_safety_limit: int = 1000
    digisac_history_initial_delay_seconds: float = 2.0
    digisac_history_request_timeout_seconds: float = 15.0
    digisac_history_max_attempts: int = 3
    digisac_history_retry_base_seconds: float = 2.0
    digisac_contact_backfill_per_page: int = 5000
    digisac_contact_hydration_interval_seconds: float = 5.0
    finalization_poll_interval_seconds: float = 0.5
    finalization_reconcile_interval_seconds: float = 5.0
    finalization_lease_seconds: int = 300
    media_status_recheck_seconds: float = 30.0
    quoted_message_max_chars: int = 240
    ia_context_safe_input_tokens: int = 96_000
    ia_context_chunk_tokens: int = 12_000
    ia_context_summary_output_tokens: int = 1_200

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
    def normalize_debug(cls, value: Any) -> Any:
        if isinstance(value, str) and value.lower() in {"release", "production"}:
            return False
        return value

    @field_validator("app_timezone")
    @classmethod
    def validate_app_timezone(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("APP_TIMEZONE must not be empty")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"APP_TIMEZONE is not a valid IANA timezone: {value}") from exc
        return value

    @field_validator("admin_api_token")
    @classmethod
    def validate_admin_api_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("ADMIN_API_TOKEN must not be empty")
        return normalized

    @field_validator("admin_ui_password", "admin_session_secret")
    @classmethod
    def validate_admin_ui_secret(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = str(info.field_name).upper()
            raise ValueError(f"{field_name} must not be empty")
        return normalized

    @field_validator(
        "digisac_history_initial_delay_seconds",
        "digisac_history_request_timeout_seconds",
        "digisac_history_retry_base_seconds",
        "digisac_contact_hydration_interval_seconds",
        "finalization_poll_interval_seconds",
        "finalization_reconcile_interval_seconds",
        "media_status_recheck_seconds",
        "audio_retry_base_seconds",
        "audio_retry_max_delay_seconds",
        "audio_retry_provider_margin_seconds",
        "image_retry_base_seconds",
        "image_retry_max_delay_seconds",
        "image_retry_provider_margin_seconds",
        "ia_retry_base_seconds",
        "ia_retry_max_delay_seconds",
        "ia_retry_provider_margin_seconds",
        "content_reconcile_interval_seconds",
        "acessorias_request_timeout_seconds",
        "acessorias_retry_base_seconds",
        "acessorias_retry_max_delay_seconds",
        "acessorias_retry_provider_margin_seconds",
    )
    @classmethod
    def positive_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("duration settings must be positive")
        return value

    @field_validator(
        "digisac_history_max_attempts",
        "digisac_contact_backfill_per_page",
        "finalization_lease_seconds",
        "quoted_message_max_chars",
        "ia_context_safe_input_tokens",
        "ia_context_chunk_tokens",
        "ia_context_summary_output_tokens",
        "acessorias_max_attempts",
        "acessorias_rate_limit_per_minute",
        "acessorias_page_safety_limit",
    )
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("limit settings must be positive")
        return value

    @field_validator("acessorias_rate_limit_per_minute")
    @classmethod
    def limit_acessorias_rate(cls, value: int) -> int:
        if value > 100:
            raise ValueError("ACESSORIAS_RATE_LIMIT_PER_MINUTE cannot exceed 100")
        return value

    @model_validator(mode="after")
    def validate_acessorias_retry_limits(self) -> "Settings":
        if self.acessorias_retry_max_delay_seconds < self.acessorias_retry_base_seconds:
            raise ValueError(
                "ACESSORIAS_RETRY_MAX_DELAY_SECONDS must be at least the base delay"
            )
        if not self.acessorias_api_base_url.strip():
            raise ValueError("ACESSORIAS_API_BASE_URL must not be empty")
        return self

    @model_validator(mode="after")
    def validate_context_limits(self) -> "Settings":
        if self.ia_context_chunk_tokens >= self.ia_context_safe_input_tokens:
            raise ValueError(
                "IA_CONTEXT_CHUNK_TOKENS must be below "
                "IA_CONTEXT_SAFE_INPUT_TOKENS"
            )
        return self


settings = Settings()


def require_admin_api_token() -> str:
    """Return the configured admin secret or fail closed at service startup."""
    token = settings.admin_api_token
    if not token:
        raise RuntimeError(
            "ADMIN_API_TOKEN is required to start the authenticated administrative API"
        )
    return token


def require_admin_ui_configuration() -> tuple[str, str]:
    """Return the UI secrets or fail closed without exposing their values."""
    password = settings.admin_ui_password
    session_secret = settings.admin_session_secret
    if not password or not session_secret:
        raise RuntimeError("Administrative UI configuration is unavailable")
    return password, session_secret
