"""
Специальные типы enum'ов для разных способов хранения в базе данных.
"""

from enum import Enum
from typing import Type, TypeVar, Any

from sqlalchemy import Enum as SQLEnum, TypeDecorator
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
		
		# Для tuple enums используем db_value, иначе value
		values = []
		for e in cls:
			# Проверяем, есть ли db_value атрибут (tuple enum)
			if hasattr(e, 'db_value'):
				values.append(e.db_value)
			else:
				values.append(str(e.value))
		return values

	def sa_enum(cls, type_name: str = None, store_as_name: bool = False, schema: str = 'social_manager'):
		"""Создает SQLAlchemy Enum с правильной обработкой tuple enums."""
		# Проверяем, используем ли tuple enum (есть db_value)
		has_db_value = any(hasattr(e, 'db_value') for e in cls)
		
		if has_db_value:
			# Для любого tuple enum нужен TypeDecorator
			class TupleEnumType(TypeDecorator):
				"""TypeDecorator для tuple enums."""
				impl = SQLEnum(
					*cls.get_db_values(store_as_name),
					name=type_name or cls.__name__.lower(),
					schema=schema,
					inherit_schema=True
				)
				cache_ok = True
				
				def process_bind_param(self, value: Any, dialect) -> str | None:
					"""Python -> Database: преобразуем enum member в нужный формат."""
					if value is None:
						return None
					if isinstance(value, cls):
						# Для store_as_name=True используем name, иначе db_value
						return value.name if store_as_name else value.db_value
					return value
				
				def process_result_value(self, value: Any, dialect) -> Any:
					"""Database -> Python: преобразуем значение в enum member."""
					if value is None:
						return None
					# Уже enum member - вернуть как есть
					if isinstance(value, cls):
						return value
					
					# Для store_as_name=True ищем по name
					if store_as_name:
						try:
							return cls[value]
						except KeyError:
							return None
					
					# Для store_as_name=False ищем по db_value
					for member in cls:
						if hasattr(member, 'db_value') and member.db_value == value:
							return member
					
					# Fallback - попробовать по name
					try:
						return cls[value]
					except KeyError:
						return None
			
			return TupleEnumType()
		else:
			# Обычный простой enum (без tuple)
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
