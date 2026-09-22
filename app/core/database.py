from functools import wraps
from typing import AsyncGenerator, Callable, TypeVar

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from .config import settings

# Тип для аннотаций
T = TypeVar("T")

# Создание async engine
async_engine = create_async_engine(
    settings.POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://"),
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_recycle=3600,  # Пересоздавать соединения каждые 60 минут
)

# Создание синхронного engine
engine = create_engine(settings.POSTGRES_URL, echo=settings.DEBUG, pool_pre_ping=True, pool_recycle=3600)

# Создаем асинхронную фабрику сессий
async_session_maker = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()

# Создаем синхронную фабрику сессий
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, bind=async_engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Асинхронный генератор сессий для FastAPI Dependency Injection.
    Использование:
            async def some_endpoint(db: AsyncSession = Depends(get_db)):
                    # ... работа с БД ...
    """
    # `async with` closes the session itself — an extra explicit close() here
    # triggers IllegalStateChangeError when this generator is finalised
    # (e.g. by the loop's async-generator shutdown).
    async with async_session_maker() as session:
        yield session


def new_session() -> AsyncSession:
    """
    Создать самостоятельную сессию (вне FastAPI DI).

    Вызывающий обязан закрыть её сам (``await session.close()``), лучше всего
    в ``finally``. Для сервисов/CLI, которые не получают сессию извне.
    """
    return async_session_maker()


def with_db_session(func: Callable[..., T]) -> Callable[..., T]:
    """
    Асинхронный декоратор для автоматического управления сессией БД.
    Пример использования:
            @with_db_session
            async def some_function():
                    # ... работа с БД ...
    """

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        # Если session уже передан, используем его
        if "session" in kwargs and kwargs["session"] is not None:
            return await func(self, *args, **kwargs)

        async with async_session_maker() as session:
            try:
                result = await func(self, *args, session=session, **kwargs)
                await session.commit()
                return result
            except Exception as e:
                await session.rollback()
                raise e

    return wrapper


async def init_db() -> None:
    """Инициализировать таблицы в БД."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
