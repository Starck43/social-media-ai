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
    DB_SCHEMA: str = "public"
    DB_TEST_SCHEMA: str = "test_schema"

    # External platform credentials (optional — integrations are enabled based on presence).
    # Application/infrastructure config, one-per-deployment: read from here. Personal
    # L2 secrets (VK user_token, Telegram MTProto parts) live in `user_credentials`.
    VK_APP_ID: Optional[str] = None
    VK_SERVICE_KEY: Optional[str] = None  # VK console "Сервисный ключ доступа" — L1 service token
    VK_CLIENT_ACCESS_KEY: Optional[str] = None  # VK console "Защищённый ключ" — OAuth client secret
    VK_API_BASE_URL: str = "https://api.vk.com/method"
    VK_OAUTH_BASE_URL: str = "https://id.vk.ru"  # VK ID (OAuth 2.1): /authorize + /oauth2/auth
    VK_REDIRECT_URI: str = "http://localhost/api/v1/social/callback"  # VK OAuth callback (must be public)
    VK_API_VERSION: str = "5.199"
    VK_REQUEST_TIMEOUT: float = 30.0

    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org"
    TELEGRAM_REQUEST_TIMEOUT: float = 30.0
    # Env fallback for the MTProto session (L2 Telegram). Prefer the personal vault
    # of the source's owner: python -m cli.main credentials login telegram --user <id>
    TELEGRAM_API_ID: Optional[str] = None
    TELEGRAM_API_HASH: Optional[str] = None
    TELEGRAM_SESSION: Optional[str] = None
    TELEGRAM_ADMIN_CHAT_ID: Optional[str] = None  # Legacy notification fallback; never a digest recipient

    # Common social collection defaults
    SOCIAL_PAGE_SIZE: int = 50
    SOCIAL_MAX_PAGES: int = 20
    SOCIAL_REQUEST_TIMEOUT: float = 30.0

    # --- Channels / owner allowlist ---
    TELEGRAM_OWNER_IDS: str = ""  # comma-separated Telegram user ids allowed to talk to the agent
    TELEGRAM_DIGEST_CHANNEL_ID: str = ""  # Deprecated, ignored by digest delivery; use workspace channel bindings

    # MAX messenger (Bot API: https://dev.max.ru)
    MAX_BOT_TOKEN: Optional[str] = None
    MAX_API_URL: str = "https://platform-api2.max.ru"
    MAX_OWNER_ID: str = ""  # MAX user id allowed to talk to the agent
    MAX_CHANNEL_ID: str = ""  # Deprecated, ignored by digest delivery; use workspace channel bindings

    # --- Tenancy ---
    # Shared bot model: clients bind their chats via invite codes
    # (/start <code>); chats are resolved to tenants from the DB.
    CREDENTIALS_KEY: Optional[str] = None  # Fernet key for tenant secrets
    DEFAULT_TENANT_SLUG: str = "owner"  # slug of the bootstrap tenant

    # --- Task runner ---
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_POLL_SECONDS: int = 30
    SCHEDULER_TIMEZONE: str = "Europe/Moscow"

    # --- Agent ---
    AGENT_MODEL: Optional[str] = None  # explicit LLMModel name; auto-resolved when empty
    AGENT_HISTORY_LIMIT: int = 20  # messages kept in agent context per session
    AGENT_MAX_ITERATIONS: int = 6  # max tool-call rounds per user message
    AGENT_DAILY_COST_LIMIT: float = 5.0  # USD cap across agent + digests per day
    AGENT_MAX_TOKENS: int = 2048  # reply size cap for chat (analysis uses LLM_DEFAULT_MAX_TOKENS)
    AGENT_TEMPERATURE: float = 0.3
    AGENT_SYSTEM_PROMPT: Optional[str] = None  # system prompt for agent chat; falls back to built-in default

    # --- Background jobs ---
    JOB_MAX_ATTEMPTS: int = 3
    JOB_RETRY_BACKOFF_SECONDS: int = 300

    LOG_LEVEL: str = "WARNING"

    def telegram_owner_ids(self) -> list[int]:
        """Parse TELEGRAM_OWNER_IDS ('1, 2, 3') into a list of ints."""
        return [int(x) for x in self.TELEGRAM_OWNER_IDS.replace(" ", "").split(",") if x]

    def max_owner_ids(self) -> list[int]:
        """Parse MAX_OWNER_ID ('1, 2') into a list of ints."""
        return [int(x) for x in self.MAX_OWNER_ID.replace(" ", "").split(",") if x]

    # LLM rate limiting
    LLM_REQUEST_DELAY: int = 1000  # Default rate limit delay in milliseconds
    LLM_DEFAULT_TEMPERATURE: float = 0.3  # Conservative temperature for analysis tasks
    LLM_DEFAULT_MAX_TOKENS: int = 400  # Optimal for social media annotations
    LLM_DEFAULT_STREAM: bool = False  # Complete responses for processing
    LLM_DEFAULT_TIMEOUT: float = 60.0  # Request timeout in seconds


settings = Settings()
