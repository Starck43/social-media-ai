"""Legacy exports and manager binding for unwired reservation storage."""

from .analysis.llm_budget import LLMBudgetDay, LLMBudgetReservation
from .managers.llm_budget_manager import LLMBudgetManager

LLMBudgetReservation.objects = LLMBudgetManager()
