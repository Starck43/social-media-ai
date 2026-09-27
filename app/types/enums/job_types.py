"""Job types for the task runner and agent tasks."""

from enum import Enum

from app.utils.db_enums import DatabaseEnum, database_enum


@database_enum
class JobType(DatabaseEnum, Enum):
    """Types of scheduled jobs/tasks."""

    COLLECT = ("collect", "Сбор данных", "📥")
    DIGEST = ("digest", "Дайджест", "📋")
    PRUNE = ("prune", "Очистка", "🧹")
    ANALYZE = ("analyze", "Анализ", "📊")
    LEARN = ("learn", "Обучение", "🧠")
    REFLECT = ("reflect", "Рефлексия", "🔄")

    def __init__(self, db_value, display_name, emoji):
        self._db_value = db_value
        self._display_name = display_name
        self._emoji = emoji

    @property
    def db_value(self) -> str:
        return self._db_value

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def emoji(self) -> str:
        return self._emoji

    @property
    def label(self) -> str:
        """Get label with emoji: '📥 Сбор данных'"""
        return f"{self._emoji} {self._display_name}"

    @classmethod
    def choices(cls, use_db_value: bool = True):
        """Get choices for form fields."""
        if use_db_value:
            return [(j.db_value, j.label) for j in cls]
        return [(j.name, j.label) for j in cls]

    @classmethod
    def get_by_value(cls, value: str):
        """Get enum by database value."""
        for job in cls:
            if job.db_value == value:
                return job
        return None

    @classmethod
    def get_by_name(cls, name: str):
        """Get enum by name."""
        try:
            return cls[name]
        except KeyError:
            return None
