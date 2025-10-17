"""
JSON Schema Builder for dynamic LLM response schemas.
"""

from typing import Dict, List, Any, Optional


class JSONSchemaBuilder:
	"""Build dynamic JSON schemas for LLM prompts."""
	
	SCHEMA_FIELDS = {
		'sentiment': {
			'sentiment_score': 'число от 0.0 (негатив) до 1.0 (позитив)',
			'sentiment_label': 'одно из: {categories}',  # Will be replaced with actual values
		},
		'topics': {
			'main_topics': 'список из {max_topics} главных тем',
		},
		'keywords': {
			'keywords': 'список из {max_keywords} ключевых слов',
		},
		'engagement': {
			'engagement_score': 'оценка от 0.0 до 1.0',
			'engagement_level': 'одно из: {levels}',  # Will be replaced with actual values
		},
		'trends': {
			'trending_topics': 'список трендовых тем',
		},
		'toxicity': {
			'toxicity_score': 'оценка от 0.0 (чистый) до 1.0 (токсичный)',
			'toxicity_category': 'одно из: {categories}',  # Will be replaced with actual values
		},
		'brand_mentions': {
			'mention_count': 'количество упоминаний бренда',
			'mention_sentiment': 'одно из: {sentiment_categories}',  # Will be replaced
		},
		'viral_detection': {
			'viral_potential': 'одно из: {potential_levels}',  # Will be replaced
			'growth_rate': 'множитель роста',
		},
	}
	
	@classmethod
	def build_schema(cls, analysis_types: List[str], scope: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
		schema = {}
		scope = scope or {}
		
		for analysis_type in analysis_types:
			if analysis_type not in cls.SCHEMA_FIELDS:
				continue
			
			type_fields = cls.SCHEMA_FIELDS[analysis_type].copy()
			
			# Get config for this type from scope (БЕЗ _config суффикса!)
			type_config = scope.get(analysis_type, {})
			
			for field_name, field_desc in type_fields.items():
				# Replace placeholders with actual config values
				if '{max_topics}' in field_desc:
					max_topics = type_config.get('max_topics', 5)
					field_desc = field_desc.replace('{max_topics}', str(max_topics))
				
				if '{max_keywords}' in field_desc:
					max_keywords = type_config.get('max_keywords', 15)
					field_desc = field_desc.replace('{max_keywords}', str(max_keywords))
				
				# Replace {categories} with actual list
				if '{categories}' in field_desc:
					categories = type_config.get('categories', [])
					if categories:
						categories_str = ', '.join([f'"{cat}"' for cat in categories])
						field_desc = field_desc.replace('{categories}', categories_str)
					else:
						field_desc = field_desc.replace('{categories}', '"значение"')
				
				# Replace {levels} with actual list
				if '{levels}' in field_desc:
					levels = type_config.get('levels', [])
					if levels:
						levels_str = ', '.join([f'"{level}"' for level in levels])
						field_desc = field_desc.replace('{levels}', levels_str)
					else:
						field_desc = field_desc.replace('{levels}', '"уровень"')
				
				# Replace {potential_levels} with actual list
				if '{potential_levels}' in field_desc:
					potential_levels = type_config.get('potential_levels', [])
					if potential_levels:
						levels_str = ', '.join([f'"{level}"' for level in potential_levels])
						field_desc = field_desc.replace('{potential_levels}', levels_str)
					else:
						field_desc = field_desc.replace('{potential_levels}', '"уровень"')
				
				# Replace {sentiment_categories} for brand_mentions
				if '{sentiment_categories}' in field_desc:
					# Get from sentiment config if available
					sentiment_config = scope.get('sentiment', {})
					sent_categories = sentiment_config.get('categories', [])
					if sent_categories:
						categories_str = ', '.join([f'"{cat}"' for cat in sent_categories])
						field_desc = field_desc.replace('{sentiment_categories}', categories_str)
					else:
						field_desc = field_desc.replace('{sentiment_categories}', '"позитив"/"негатив"/"нейтрал"')
				
				schema[field_name] = field_desc
		
		return schema
	
	@classmethod
	def format_schema_as_json(cls, schema: Dict[str, str]) -> str:
		if not schema:
			return '{}'
		
		lines = ['{']
		items = list(schema.items())
		for i, (field_name, field_desc) in enumerate(items):
			comma = ',' if i < len(items) - 1 else ''
			lines.append(f'\t"{field_name}": "{field_desc}"{comma}')
		lines.append('}')
		return '\n'.join(lines)
	
	@classmethod
	def build_json_instruction(cls, analysis_types: List[str], scope: Optional[Dict[str, Any]] = None) -> str:
		schema = cls.build_schema(analysis_types, scope)
		
		if not schema:
			return """

ВАЖНО: Верни результат СТРОГО в JSON формате.

Не добавляй текст до или после JSON.
"""
		
		schema_json = cls.format_schema_as_json(schema)
		
		return f"""

ВАЖНО: Верни результат СТРОГО в JSON формате:
{schema_json}

Не добавляй текст до или после JSON.
"""
