"""Read-only subsystem projections of resolved flat Settings, never env loaders.

Views are fresh snapshots on access: consumers do not keep a second configuration
store. SecretStr requires explicit unwrapping at an execution boundary.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, SecretStr

SENSITIVE_FIELDS = frozenset(
    {
        "SECRET_KEY",
        "POSTGRES_URL",
        "REDIS_URL",
        "CREDENTIALS_KEY",
        "VK_SERVICE_KEY",
        "VK_CLIENT_ACCESS_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION",
        "MAX_BOT_TOKEN",
        "YOOKASSA_SECRET_KEY",
    }
)


class SettingsView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def from_settings(cls, settings) -> Self:
        return cls(**{name: getattr(settings, name) for name in cls.model_fields})


class DatabaseSettingsView(SettingsView):
    POSTGRES_URL: SecretStr
    DB_SCHEMA: str
    DB_TEST_SCHEMA: str
    DB_POOL: str


class SecuritySettingsView(SettingsView):
    SECRET_KEY: SecretStr
    CREDENTIALS_KEY: SecretStr | None
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int
    PASSWORD_MIN_LENGTH: int
    PASSWORD_REQUIRE_UPPERCASE: bool
    PASSWORD_REQUIRE_LOWERCASE: bool
    PASSWORD_REQUIRE_NUMBERS: bool
    PASSWORD_REQUIRE_SPECIAL: bool


class CollectionSettingsView(SettingsView):
    SOURCE_STALE_HOURS: int
    SOCIAL_PAGE_SIZE: int
    SOCIAL_MAX_PAGES: int
    SOCIAL_COLLECT_LIMIT: int
    SOCIAL_COLLECT_HARD_LIMIT: int
    SOCIAL_REQUEST_TIMEOUT: float
    SOCIAL_RATE_LIMIT_RETRIES: int
    SOCIAL_RATE_LIMIT_MAX_WAIT: float


class LLMSettingsView(SettingsView):
    LLM_REQUEST_DELAY: int
    LLM_DEFAULT_TEMPERATURE: float
    LLM_DEFAULT_MAX_TOKENS: int
    LLM_DEFAULT_STREAM: bool
    LLM_DEFAULT_TIMEOUT: float
