"""
Скрипт для создания реалистичных тестовых данных аналитики.

Создает:
- 2 источника (VK группы)
- 2 сценария анализа
- Реалистичные AI аналитики за последние 30 дней
"""
import asyncio
import random
import sys
from datetime import timedelta, date
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.database import init_db, async_session_maker
from app.models import Platform, Source, BotScenario, AIAnalytics
from app.types import SourceType, PlatformType

# Realistic topics for different scenarios
TECH_TOPICS = [
    "Искусственный интеллект", "Машинное обучение", "Нейросети",
    "ChatGPT", "Python", "JavaScript", "React", "Django",
    "Кибербезопасность", "Облачные технологии", "DevOps",
    "Blockchain", "Криптовалюты", "Web3", "Метавселенные"
]

BUSINESS_TOPICS = [
    "Маркетинг", "Продажи", "Стартапы", "Инвестиции",
    "E-commerce", "CRM системы", "Аналитика данных",
    "Управление проектами", "Agile", "Scrum",
    "Финансы", "Бизнес-модели", "MVP", "Product Market Fit"
]

SENTIMENT_LABELS = ["positive", "neutral", "negative"]


async def create_test_data():
    """Create realistic test data."""
    
    async with async_session_maker() as session:
        print("🚀 Starting test data generation...\n")
        
        # 1. Get or create VK platform
        print("1️⃣  Checking platform...")
        platform = await Platform.objects.filter(name="VK").first()
        
        if not platform:
            platform = await Platform.objects.create(
                name="VK",
                platform_type=PlatformType.VK,
                base_url="https://vk.com",
                params={
                    "api_base_url": "https://api.vk.com/method",
                    "api_version": "5.131"
                },
                is_active=True
            )
            print(f"   ✅ Created platform: {platform.name}")
        else:
            print(f"   ✅ Found platform: {platform.name}")
        
        # 2. Get or use existing scenarios
        print("\n2️⃣  Getting scenarios...")
        
        scenarios_list = await BotScenario.objects.filter(is_active=True).limit(2)
        
        if len(scenarios_list) < 2:
            print(f"   ⚠️  Found only {len(scenarios_list)} scenarios, need at least 2")
            print("   💡 Please create scenarios in admin panel first")
            print("   🔗 http://localhost:8000/admin/botscenario/create")
            return
        
        tech_scenario = scenarios_list[0]
        business_scenario = scenarios_list[1]
        
        print(f"   ✅ Using scenario 1: {tech_scenario.name}")
        print(f"   ✅ Using scenario 2: {business_scenario.name}")
        
        # 3. Create sources
        print("\n3️⃣  Creating sources...")
        
        tech_source = await Source.objects.filter(external_id="tech_news_daily").first()
        if not tech_source:
            tech_source = await Source.objects.create(
                platform_id=platform.id,
                name="Tech News Daily",
                source_type=SourceType.GROUP,
                external_id="tech_news_daily",
                params={"description": "Технологические новости каждый день"},
                is_active=True,
                bot_scenario_id=tech_scenario.id
            )
            print(f"   ✅ Created source: {tech_source.name}")
        else:
            tech_source.bot_scenario_id = tech_scenario.id
            await tech_source.save()
            print(f"   ✅ Found source: {tech_source.name}")
        
        business_source = await Source.objects.filter(external_id="business_insights").first()
        if not business_source:
            business_source = await Source.objects.create(
                platform_id=platform.id,
                name="Business Insights Hub",
                source_type=SourceType.GROUP,
                external_id="business_insights",
                params={"description": "Бизнес инсайты и кейсы"},
                is_active=True,
                bot_scenario_id=business_scenario.id
            )
            print(f"   ✅ Created source: {business_source.name}")
        else:
            business_source.bot_scenario_id = business_scenario.id
            await business_source.save()
            print(f"   ✅ Found source: {business_source.name}")
        
        # 4. Delete old analytics for these sources
        print("\n4️⃣  Cleaning old analytics...")
        old_analytics = await AIAnalytics.objects.filter(
            AIAnalytics.source_id.in_([tech_source.id, business_source.id])
        )
        for analytics in old_analytics:
            await analytics.delete()
        print(f"   ✅ Deleted {len(old_analytics)} old analytics")
        
        # 5. Generate analytics for last 30 days
        print("\n5️⃣  Generating analytics...")
        
        start_date = date.today() - timedelta(days=30)
        analytics_count = 0
        
        # LLM providers for rotation
        providers = ["openai", "deepseek", "anthropic"]
        models = {
            "openai": ["gpt-4o-mini", "gpt-4o"],
            "deepseek": ["deepseek-chat"],
            "anthropic": ["claude-3-haiku-20240307"]
        }
        
        for day_offset in range(31):  # 0-30 days
            current_date = start_date + timedelta(days=day_offset)
            
            # Tech source - 2-4 analyses per day
            num_tech = random.randint(2, 4)
            for _ in range(num_tech):
                provider = random.choice(providers)
                model = random.choice(models[provider])
                
                # Random topics (1-3)
                num_topics = random.randint(1, 3)
                topics = random.sample(TECH_TOPICS, num_topics)
                
                # Create topics structure
                topics_data = []
                for topic in topics:
                    topics_data.append({
                        "topic": topic,
                        "prevalence": round(random.uniform(0.2, 0.8), 2),
                        "sentiment": random.choice(SENTIMENT_LABELS),
                        "confidence": round(random.uniform(0.7, 0.95), 2)
                    })
                
                # Sentiment score (-1 to 1)
                sentiment_score = round(random.uniform(-0.5, 0.8), 2)
                
                # Token usage
                request_tokens = random.randint(500, 1500)
                response_tokens = random.randint(200, 800)
                
                # Estimated cost (per 1M tokens)
                if provider == "openai":
                    cost_per_1m = 0.15 if "gpt-4o-mini" in model else 5.0
                elif provider == "deepseek":
                    cost_per_1m = 0.14
                else:  # anthropic
                    cost_per_1m = 0.25
                
                total_tokens = request_tokens + response_tokens
                estimated_cost = (total_tokens / 1_000_000) * cost_per_1m
                
                # Create analytics
                analytics = await AIAnalytics.objects.create(
                    source_id=tech_source.id,
                    analysis_date=current_date,
                    summary_data={
                        "summary": f"Анализ технологических постов: {', '.join(topics)}",
                        "sentiment": {
                            "score": sentiment_score,
                            "label": "positive" if sentiment_score > 0.3 else "negative" if sentiment_score < -0.3 else "neutral"
                        },
                        "topics": topics_data,
                        "key_points": [f"Обсуждение {topic}" for topic in topics]
                    },
                    sentiment_score=sentiment_score,
                    request_tokens=request_tokens,
                    response_tokens=response_tokens,
                    estimated_cost=estimated_cost,
                    provider_type=provider,
                    media_types=["text"]
                )
                analytics_count += 1
            
            # Business source - 1-3 analyses per day
            num_business = random.randint(1, 3)
            for _ in range(num_business):
                provider = random.choice(providers)
                model = random.choice(models[provider])
                
                # Random topics (1-2)
                num_topics = random.randint(1, 2)
                topics = random.sample(BUSINESS_TOPICS, num_topics)
                
                topics_data = []
                for topic in topics:
                    topics_data.append({
                        "topic": topic,
                        "prevalence": round(random.uniform(0.3, 0.9), 2),
                        "sentiment": random.choice(SENTIMENT_LABELS),
                        "confidence": round(random.uniform(0.75, 0.95), 2)
                    })
                
                sentiment_score = round(random.uniform(-0.3, 0.9), 2)
                
                request_tokens = random.randint(400, 1200)
                response_tokens = random.randint(150, 600)
                
                if provider == "openai":
                    cost_per_1m = 0.15 if "gpt-4o-mini" in model else 5.0
                elif provider == "deepseek":
                    cost_per_1m = 0.14
                else:
                    cost_per_1m = 0.25
                
                total_tokens = request_tokens + response_tokens
                estimated_cost = (total_tokens / 1_000_000) * cost_per_1m
                
                analytics = await AIAnalytics.objects.create(
                    source_id=business_source.id,
                    analysis_date=current_date,
                    summary_data={
                        "summary": f"Анализ бизнес контента: {', '.join(topics)}",
                        "sentiment": {
                            "score": sentiment_score,
                            "label": "positive" if sentiment_score > 0.3 else "negative" if sentiment_score < -0.3 else "neutral"
                        },
                        "topics": topics_data,
                        "key_points": [f"Инсайт по {topic}" for topic in topics]
                    },
                    sentiment_score=sentiment_score,
                    request_tokens=request_tokens,
                    response_tokens=response_tokens,
                    estimated_cost=estimated_cost,
                    provider_type=provider,
                    media_types=["text"]
                )
                analytics_count += 1
            
            if (day_offset + 1) % 5 == 0:
                print(f"   📅 Generated analytics for {day_offset + 1} days...")
        
        print(f"\n   ✅ Created {analytics_count} analytics records")
        
        # 6. Summary
        print("\n" + "="*60)
        print("✨ Test data generation complete!")
        print("="*60)
        print(f"\nCreated:")
        print(f"  • Platform: VK")
        print(f"  • Sources: 2")
        print(f"    - {tech_source.name} (Tech)")
        print(f"    - {business_source.name} (Business)")
        print(f"  • Scenarios: 2")
        print(f"    - {tech_scenario.name}")
        print(f"    - {business_scenario.name}")
        print(f"  • Analytics: {analytics_count} (last 30 days)")
        print(f"\n📊 View in dashboard:")
        print(f"   http://localhost:8000/dashboard")
        print(f"\n🔗 View topic chains:")
        print(f"   http://localhost:8000/dashboard/topic-chains")
        print()


async def main():
    """Main entry point."""
    await init_db()
    await create_test_data()


if __name__ == "__main__":
    asyncio.run(main())
