"""Stable role identifiers and structured permission actions."""

from enum import Enum

from app.utils.db_enums import DatabaseEnum, database_enum


class PlatformRoleType(str, Enum):
    USER = "user"
    OPERATOR = "operator"
    ADMIN = "admin"

    def __str__(self):
        return self.value


class WorkspaceRoleType(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    ANALYST = "analyst"
    VIEWER = "viewer"

    def __str__(self):
        return self.value


@database_enum
class ActionType(DatabaseEnum, Enum):
    """User permission action types."""

    # Format: NAME = ("db_value", "Display Name", "Emoji")
    VIEW = ("view", "Просмотр", "👀")
    CREATE = ("create", "Создание", "➕")
    UPDATE = ("update", "Изменение", "✏️")
    DELETE = ("delete", "Удаление", "🗑️")
    ANALYZE = ("analyze", "Анализ", "📊")
    MODERATE = ("moderate", "Модерация", "🛡️")
    EXPORT = ("export", "Экспорт", "📤")
    CONFIGURE = ("configure", "Настройка", "⚙️")

    def __init__(self, db_value, display_name, emoji):
        self._db_value = db_value
        self._display_name = display_name
        self._emoji = emoji

    @property
    def db_value(self):
        return self._db_value

    @property
    def display_name(self):
        return self._display_name

    @property
    def emoji(self):
        return self._emoji

    @property
    def label(self):
        """Get label with emoji."""
        return f"{self._emoji} {self._display_name}"

    @classmethod
    def choices(cls, use_db_value: bool = False):
        """Get choices for form fields (store_as_name=True by default)."""
        if use_db_value:
            return [(a.db_value, a.label) for a in cls]
        return [(a.name, a.label) for a in cls]

    @classmethod
    def get_by_value(cls, value: str):
        """Get enum by database value."""
        for action in cls:
            if action.db_value == value:
                return action
        return None

    @classmethod
    def get_by_name(cls, name: str):
        """Get enum by name."""
        try:
            return cls[name]
        except KeyError:
            return None
