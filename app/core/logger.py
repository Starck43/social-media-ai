import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings

# Создаем директорию для логов, если её нет
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

# Формат логов
log_format = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Создаем логгер
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)

# Консольный обработчик
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

# Файловый обработчик с ротацией
file_handler = RotatingFileHandler(
    log_dir / 'app.log',
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setFormatter(log_format)

# Добавляем обработчики к логгеру
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# В продакшн режиме можно установить более высокий уровень логирования
if settings.ENVIRONMENT == "production":
    logger.setLevel(logging.WARNING)
    # Отключаем логирование отладочной информации от сторонних библиотек
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
