from typing import Any, Dict, List, Optional, Union
from pydantic import AnyHttpUrl, EmailStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional
from dotenv import load_dotenv
from typing import List

load_dotenv()


class Settings(BaseSettings):

	# Настройки приложения
	ENVIRONMENT: str = "development"
	ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8000,http://localhost:8501"
	BACKEND_CORS_ORIGINS: List[str] = [
		"http://localhost:3000",  # React dev server
		"http://localhost:8000",  # FastAPI dev server
		# "https://ваш-продакшн-домен.com",
	]
	HOST: str = "0.0.0.0"
	PORT: int = 8000
	SECRET_KEY: str
	DEBUG: bool = True

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
	REDIS_URL: str
	DB_SCHEMA: str = 'social_manager'

	# Опциональные переменные
	DEEPSEEK_API_KEY: str = ""
	VK_APP_ID: str = ""
	VK_APP_SECRET: str = ""
	TELEGRAM_BOT_TOKEN: str = ""

	model_config = SettingsConfigDict(
		env_file=".env",
		env_file_encoding="utf-8",
		extra="ignore"  # Игнорируем лишние переменные в .env
	)


settings = Settings()
