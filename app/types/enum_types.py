"""
Специальные типы enum'ов для разных способов хранения в базе данных.
"""

from enum import Enum
from typing import Type, TypeVar

from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import mapped_column

E = TypeVar('E', bound=Enum)


def database_enum(enum_class: Type[E]) -> Type[E]:
	"""Декоратор для добавления методов работы с БД к enum'ам."""

	# ⚡️ Создаем обычные функции и превращаем их в classmethod
	def get_db_values(cls, store_as_name: bool) -> list[str]:
		"""
		Получает значения для БД.
		
		Args:
			store_as_name: Принудительно указать способ хранения (True = как имена, False = как значения)
		"""
		if store_as_name:
			return [e.name for e in cls]  # храним как имена
		return [str(e.value) for e in cls]  # храним как значения

	def sa_enum(cls, type_name: str = None, store_as_name: bool = False, schema: str = 'social_manager'):
		"""Создает SQLAlchemy Enum."""
		return SQLEnum(
			*cls.get_db_values(store_as_name),
			name=type_name or cls.__name__.lower(),
			schema=schema,
			inherit_schema=True
		)

	def sa_column(cls, default=None, nullable=False, type_name: str = None, store_as_name: bool = False, **kwargs):
		"""
		Создает колонку с возможностью указать способ хранения enum.
		
		Args:
			default: Значение по умолчанию
			nullable: Может ли быть NULL
			type_name: Имя типа в БД
			store_as_name: Как хранить enum (True = как имена, False = как значения, None = по умолчанию)
			**kwargs: Другие параметры для mapped_column
		"""
		return mapped_column(
			cls.sa_enum(type_name, store_as_name),
			default=default,
			nullable=nullable,
			**kwargs
		)

	# ⚡️ Присваиваем методы как classmethod
	enum_class.get_db_values = classmethod(get_db_values)
	enum_class.sa_enum = classmethod(sa_enum)
	enum_class.sa_column = classmethod(sa_column)

	return enum_class
