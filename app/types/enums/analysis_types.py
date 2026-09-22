"""Analysis-related enum types."""

from enum import Enum

from app.utils.db_enums import DatabaseEnum, database_enum


@database_enum
class AnalysisType(DatabaseEnum, Enum):
    """Types of AI analysis."""

    # Format: NAME = ("db_value", "Display Name", "Emoji")

    # Core analysis types (v1.0)
    SENTIMENT = ("sentiment", "Анализ тональности", "😊")
    TRENDS = ("trends", "Обнаружение трендов", "📈")
    ENGAGEMENT = ("engagement", "Вовлеченность", "👥")
    KEYWORDS = ("keywords", "Ключевые слова", "🔍")
    TOPICS = ("topics", "Темы", "💡")
    TOXICITY = ("toxicity", "Токсичность", "⚠️")
    DEMOGRAPHICS = ("demographics", "Демография", "👤")

    # Extended analysis types (v2.0)
    VIRAL_DETECTION = ("viral_detection", "Вирусный потенциал", "🔥")
    INFLUENCER_ACTIVITY = ("influencer", "Активность инфлюенсеров", "⭐")
    COMPETITOR_TRACKING = ("competitor", "Мониторинг конкурентов", "🔎")
    CUSTOMER_INTENT = ("intent", "Намерения клиентов", "🎯")
    BRAND_MENTIONS = ("brand_mentions", "Упоминания бренда", "🏷️")
    HASHTAG_ANALYSIS = ("hashtag_analysis", "Анализ хэштегов", "#️⃣")

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
    def choices(cls, use_db_value: bool = True):
        """Get choices for form fields."""
        if use_db_value:
            return [(a.db_value, a.label) for a in cls]
        return [(a.name, a.label) for a in cls]

    @classmethod
    def get_by_value(cls, value: str):
        """Get enum by database value."""
        for analysis in cls:
            if analysis.db_value == value:
                return analysis
        return None

    @classmethod
    def get_by_name(cls, name: str):
        """Get enum by name."""
        try:
            return cls[name]
        except KeyError:
            return None


@database_enum
class SentimentLabel(DatabaseEnum, Enum):
    """Sentiment analysis labels."""

    # Format: NAME = ("db_value", "Display Name", "Emoji")
    POSITIVE = ("positive", "Позитивный", "😊")
    NEGATIVE = ("negative", "Негативный", "😞")
    NEUTRAL = ("neutral", "Нейтральный", "😐")
    MIXED = ("mixed", "Смешанный", "🤔")

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
    def choices(cls, use_db_value: bool = True):
        """Get choices for form fields."""
        if use_db_value:
            return [(s.db_value, s.label) for s in cls]
        return [(s.name, s.label) for s in cls]

    @classmethod
    def get_by_value(cls, value: str):
        """Get enum by database value."""
        for sentiment in cls:
            if sentiment.db_value == value:
                return sentiment
        return None

    @classmethod
    def get_by_name(cls, name: str):
        """Get enum by name."""
        try:
            return cls[name]
        except KeyError:
            return None


@database_enum
class PeriodType(DatabaseEnum, Enum):
    """Types of analysis periods."""

    # Format: NAME = ("db_value", "Display Name", "Emoji")
    DAILY = ("daily", "Ежедневно", "📅")
    WEEKLY = ("weekly", "Еженедельно", "📆")
    MONTHLY = ("monthly", "Ежемесячно", "📊")
    CUSTOM = ("custom", "Пользовательский", "⚙️")

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

    def __str__(self) -> str:
        return self.display_name

    @property
    def label(self):
        """Get label with emoji."""
        return f"{self._emoji} {self._display_name}"

    @classmethod
    def choices(cls, use_db_value: bool = False):
        """Get choices for form fields (store_as_name=True by default)."""
        if use_db_value:
            return [(p.db_value, p.label) for p in cls]
        return [(p.name, p.label) for p in cls]

    @classmethod
    def get_by_value(cls, value: str):
        """Get enum by database value."""
        for period in cls:
            if period.db_value == value:
                return period
        return None

    @classmethod
    def get_by_name(cls, name: str):
        """Get enum by name."""
        try:
            return cls[name]
        except KeyError:
            return None
