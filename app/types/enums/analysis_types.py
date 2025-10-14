"""Analysis-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class AnalysisType(Enum):
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


@database_enum
class SentimentLabel(Enum):
	"""Sentiment analysis labels."""
	POSITIVE = "positive"
	NEGATIVE = "negative"
	NEUTRAL = "neutral"
	MIXED = "mixed"


@database_enum
class PeriodType(Enum):
	"""Types of analysis periods."""
	DAILY = "Ежедневно"
	WEEKLY = "Еженедельно"
	MONTHLY = "Ежемесячно"
	CUSTOM = "Пользовательский"

	def __str__(self) -> str:
		return str(self.value)

	@property
	def display_name(self) -> str:
		return self.__str__()
