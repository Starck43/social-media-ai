from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Игнорируем лишние переменные в .env
    )

    # Настройки приложения
    ENVIRONMENT: str = "development"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8000,http://localhost:8501"
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",  # React dev server
        "http://localhost:8000",  # FastAPI dev server
        # "https://real-domain.com",
    ]
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SECRET_KEY: str
    DEBUG: bool = False

    # Admin settings
    ADMIN_ENABLED: bool = True

    # Rate limiting settings
    RESET_PASSWORD_RATE_LIMIT: str = "3/hour"
    CHANGE_PASSWORD_RATE_LIMIT: str = "5/minute"
    LOGIN_TIMEOUT_MINUTES: int = 15  # Lockout duration after max attempts
    MAX_LOGIN_ATTEMPTS: int = 5

    # Password settings
    PASSWORD_MIN_LENGTH: int = 6 if ENVIRONMENT == "development" else 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_LOWERCASE: bool = True
    PASSWORD_REQUIRE_NUMBERS: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = False  # Set to True to require special characters

    # API настройки
    API_V1_STR: str = "/api/v1"
    TOKEN_URL: str = f"{API_V1_STR}/auth/login"

    # JWT настройки
    ALGORITHM: str = "HS256"
    # Токен доступа истекает через 30 дней в development и 1 час в production
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30
    # Refresh токен истекает через 30 дней
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    def get_token_expire_minutes(self) -> int:
        """Get token expiration time in minutes based on environment."""
        if self.ENVIRONMENT == "production":
            return 60  # 1 час в production
        return self.ACCESS_TOKEN_EXPIRE_MINUTES  # 30 дней в development

    # SCRF настройки
    SCRF_TOKEN_EXPIRY_MINUTES: int = 15
    SCRF_TOKEN_LENGTH: int = 32

    POSTGRES_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"  # Optional: only needed by frozen Celery/sqladmin storage
    DB_SCHEMA: str = "social_manager"

    # External platform credentials (optional — integrations are enabled based on presence)
    VK_APP_ID: Optional[str] = None
    VK_SERVICE_ACCESS_TOKEN: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ADMIN_CHAT_ID: Optional[str] = None  # Legacy: default chat for admin notifications

    # --- Channels / owner allowlist ---
    TELEGRAM_OWNER_IDS: str = ""  # comma-separated Telegram user ids allowed to talk to the agent
    TELEGRAM_DIGEST_CHANNEL_ID: str = ""  # target channel/chat id for scheduled digests

    # MAX messenger (Bot API: https://dev.max.ru)
    MAX_BOT_TOKEN: Optional[str] = None
    MAX_API_BASE: str = "https://platform-api2.max.ru"
    MAX_OWNER_ID: str = ""  # MAX user id allowed to talk to the agent
    MAX_CHANNEL_ID: str = ""  # target channel/chat id for scheduled digests

    # --- Scheduler ---
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_POLL_SECONDS: int = 30
    SCHEDULER_TIMEZONE: str = "Europe/Moscow"

    # --- Agent ---
    AGENT_MODEL: Optional[str] = None  # explicit LLMModel name; auto-resolved when empty
    AGENT_HISTORY_LIMIT: int = 20  # messages kept in agent context per session
    AGENT_MAX_ITERATIONS: int = 6  # max tool-call rounds per user message
    AGENT_DAILY_COST_LIMIT: float = 5.0  # USD cap across agent + digests per day

    # --- Background jobs ---
    JOB_MAX_ATTEMPTS: int = 3
    JOB_RETRY_BACKOFF_SECONDS: int = 300

    LOG_LEVEL: str = "INFO"

    def telegram_owner_ids(self) -> list[int]:
        """Parse TELEGRAM_OWNER_IDS ('1, 2, 3') into a list of ints."""
        return [int(x) for x in self.TELEGRAM_OWNER_IDS.replace(" ", "").split(",") if x]

    def max_owner_ids(self) -> list[int]:
        """Parse MAX_OWNER_ID ('1, 2') into a list of ints."""
        return [int(x) for x in self.MAX_OWNER_ID.replace(" ", "").split(",") if x]

    # LLM rate limiting
    LLM_REQUEST_DELAY: int = 3000  # Default rate limit delay in milliseconds
    LLM_DEFAULT_TEMPERATURE: float = 0.3  # Conservative temperature for analysis tasks
    LLM_DEFAULT_MAX_TOKENS: int = 400  # Optimal for social media annotations
    LLM_DEFAULT_STREAM: bool = False  # Complete responses for processing
    LLM_DEFAULT_TIMEOUT: float = 90.0  # Request timeout in seconds


settings = Settings()
