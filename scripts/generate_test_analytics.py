#!/usr/bin/env python3
"""
Скрипт для генерации тестовых данных аналитики для демонстрации цепочек тем.

Создает реалистичные данные в таблице AIAnalytics с заполненными полями:
- topic_chain_id для цепочек тем
- response_payload с данными анализа
- summary_data с агрегированными результатами
"""

import asyncio
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

from app.core.database import async_engine
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


async def create_test_analytics_data():
	"""Создать тестовые данные аналитики для демонстрации цепочек тем."""

	async with async_engine.begin() as conn:
		# Получить существующие источники для связи
		sources = await conn.run_sync(lambda sync_conn: sync_conn.execute(
			text("SELECT id, name FROM social_manager.sources WHERE is_active = true LIMIT 5")
		).fetchall())

		if not sources:
			print("❌ Нет активных источников. Создайте источники сначала.")
			return

		# Получить сценарии для разнообразия
		scenarios = await conn.run_sync(lambda sync_conn: sync_conn.execute(
			text("SELECT id, name FROM social_manager.bot_scenarios LIMIT 3")
		).fetchall())

		print(f"📊 Создаю тестовые данные для {len(sources)} источников...")

		# Тестовые цепочки тем
		topic_chains = [
			{
				"id": "brand_monitoring_001",
				"name": "Мониторинг бренда X",
				"description": "Отслеживание упоминаний бренда X в соцсетях"
			},
			{
				"id": "competitor_analysis_002",
				"name": "Анализ конкурентов",
				"description": "Сравнение с конкурентами в нише"
			},
			{
				"id": "crisis_detection_003",
				"name": "Обнаружение кризисов",
				"description": "Раннее выявление негативных трендов"
			},
			{
				"id": "product_feedback_004",
				"name": "Отзывы о продуктах",
				"description": "Анализ обратной связи о продуктах"
			}
		]

		# Шаблоны тем для разных цепочек
		chain_topics = {
			"brand_monitoring_001": [
				"Бренд X", "Качество продуктов", "Цена", "Обслуживание", "Рекомендации"
			],
			"competitor_analysis_002": [
				"Бренд X vs Конкурент Y", "Сравнение цен", "Качество сервиса", "Инновации"
			],
			"crisis_detection_003": [
				"Негативные отзывы", "Проблемы с доставкой", "Качество продукции", "Служба поддержки"
			],
			"product_feedback_004": [
				"Новый продукт Z", "Функциональность", "Дизайн", "Цена", "Качество"
			]
		}

		# Генерация данных за последние 30 дней
		base_date = date.today() - timedelta(days=30)

		for day_offset in range(31):  # 31 день данных
			current_date = base_date + timedelta(days=day_offset)

			for source_id, source_name in sources:
				# Выбрать случайную цепочку тем для источника
				chain = random.choice(topic_chains)
				chain_id = chain["id"]
				topics = chain_topics[chain_id]

				# Создать response_payload с данными анализа
				response_payload = {
					"multi_llm_analysis": {
						"text_analysis": {
							"parsed": {
								"topic_analysis": {
									"main_topics": random.sample(topics, min(3, len(topics))),
									"topic_prevalence": {topic: round(random.uniform(0.1, 0.8), 2)
									                     for topic in topics[:3]},
									"emerging_topics": random.sample(topics, min(2, len(topics)))
								},
								"sentiment_analysis": {
									"overall_sentiment": random.choice(["positive", "negative", "neutral"]),
									"sentiment_score": round(random.uniform(0.2, 0.9), 2),
									"dominant_emotions": random.sample(
										["радость", "удивление", "гнев", "отвращение", "страх"], 2)
								}
							},
							"request": {
								"model": random.choice(["gpt-4", "claude-3", "deepseek-chat"]),
								"prompt": f"Анализ контента источника {source_name} за {current_date}"
							},
							"response": {
								"choices": [{"message": {"content": "Тестовый анализ..."}}],
								"usage": {"total_tokens": 1500}
							}
						}
					}
				}

				# Создать summary_data
				summary_data = {
					"ai_analysis": {
						"topic_analysis": {
							"main_topics": random.sample(topics, min(3, len(topics))),
							"topic_prevalence": {topic: round(random.uniform(0.1, 0.8), 2)
							                     for topic in topics[:3]}
						},
						"sentiment_analysis": {
							"overall_sentiment": random.choice(["positive", "negative", "neutral"]),
							"sentiment_score": round(random.uniform(0.2, 0.9), 2)
						}
					},
					"content_statistics": {
						"total_posts": random.randint(10, 100),
						"avg_reactions_per_post": round(random.uniform(5, 50), 1),
						"total_comments": random.randint(20, 200)
					},
					"source_metadata": {
						"source_type": random.choice(["VK", "Telegram"]),
						"platform": "VK" if "VK" in source_name else "Telegram",
						"source_name": source_name
					}
				}

				# Вставить данные в таблицу
				await conn.run_sync(lambda sync_conn: sync_conn.execute(
					text("""
                    INSERT INTO social_manager.ai_analytics
                    (source_id, analysis_date, period_type, topic_chain_id, summary_data,
                     llm_model, prompt_text, response_payload, created_at, updated_at)
                    VALUES (:source_id, :analysis_date, :period_type, :topic_chain_id, :summary_data,
                           :llm_model, :prompt_text, :response_payload, :created_at, :updated_at)
                    """),
					{
						"source_id": source_id,
						"analysis_date": current_date,
						"period_type": "DAILY",
						"topic_chain_id": chain_id,
						"summary_data": json.dumps(summary_data),
						"llm_model": random.choice(["gpt-4", "claude-3", "deepseek-chat"]),
						"prompt_text": f"Анализ контента за {current_date}",
						"response_payload": json.dumps(response_payload),
						"created_at": datetime.now(),
						"updated_at": datetime.now()
					}
				))

				print(f"✅ Создан анализ для источника {source_name} ({current_date}) - цепочка {chain['name']}")

		print(f"\n🎉 Тестовые данные созданы! Всего записей: {len(sources) * 31}")
		print("📊 Цепочки тем доступны в дашборде: /dashboard/topic-chains")


if __name__ == "__main__":
	asyncio.run(create_test_analytics_data())
